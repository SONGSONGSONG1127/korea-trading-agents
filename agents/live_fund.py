# -*- coding: utf-8 -*-
"""
라이브 펀드 — 시뮬 엔진의 규칙(버퍼 리밸런싱)을 실계좌(모의)에 미래 방향으로 적용

구조:
  _펀드설정 시트: [계좌, 시장, 종목수, 리밸주기(거래일), 비중방식, 개시일, 최종리밸일]
  매일 아침 브리핑에서 process_rebalances() 호출:
    리밸 주기 도래한 펀드 계좌 → 멀티팩터 스마트랭킹 재실행 →
    버퍼 규칙(보유 종목은 랭크 3×N 밖으로 밀릴 때만 편출) →
    편출 대금으로 신규 상위 종목 편입(상관 분산 적용) → _매매이력 기록

원칙: 펀드 시뮬에서 검증된 규칙 그대로 — 새 규칙 발명 없음.
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

CONFIG_SHEET = "_펀드설정"
CONFIG_HEADER = ["계좌", "시장", "종목수", "리밸주기", "비중방식", "개시일", "최종리밸일"]
_COST = 0.003
_CORR_CAP = 0.65


def _config_ws():
    import gspread
    from .portfolio_agent import _spreadsheet
    sh = _spreadsheet()
    try:
        ws = sh.worksheet(CONFIG_SHEET)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=CONFIG_SHEET, rows=100, cols=10)
        ws.update(values=[CONFIG_HEADER], range_name="A1")
    return ws


def save_config(account: str, market: str, n_stocks: int,
                rebalance_days: int, weighting: str, start_date: str) -> None:
    _config_ws().append_row([account, market, n_stocks, rebalance_days,
                             weighting, start_date, start_date])


def load_configs() -> list[dict]:
    return _config_ws().get_all_records()


def _update_last_rebal(account: str, date_str: str) -> None:
    ws = _config_ws()
    col = ws.col_values(1)
    for i, val in enumerate(col[1:], start=2):
        if str(val) == account:
            ws.update_cell(i, 7, date_str)
            return


def _trading_days_since(date_str: str, bench: pd.DataFrame) -> int:
    try:
        d0 = pd.Timestamp(str(date_str))
    except ValueError:
        return 0
    dates = pd.to_datetime(bench["date"])
    return int((dates > d0).sum())


def process_rebalances(dry: bool = False) -> list[str]:
    """리밸 주기 도래한 펀드 계좌들을 처리. 텔레그램용 메시지 라인 반환."""
    from . import factors, portfolio_agent, recommend_agent

    try:
        configs = load_configs()
    except Exception as e:
        return [f"(펀드설정 로드 실패: {e})"]
    if not configs:
        return []

    lines: list[str] = []
    today = datetime.now().strftime("%Y-%m-%d")
    data_cache: dict[str, tuple] = {}  # market -> (data, bench, meta, ranked)

    for cfg in configs:
        account = str(cfg.get("계좌", ""))
        market = str(cfg.get("시장", "KR"))
        try:
            n_stocks = int(cfg.get("종목수", 10))
            rebal_days = int(cfg.get("리밸주기", 20))
        except (TypeError, ValueError):
            continue
        last = str(cfg.get("최종리밸일") or cfg.get("개시일") or today)

        # 주기 도래 판정 (벤치마크 거래일 기준)
        try:
            if market not in data_cache:
                data, bench, meta = recommend_agent.load_market_data(market)
                sr = factors.smart_rank(data, bench, n_top=n_stocks * 4)
                data_cache[market] = (data, bench, meta, sr)
            data, bench, meta, sr = data_cache[market]
        except Exception as e:
            lines.append(f"🏦 {account}: 데이터 로드 실패 — {e}")
            continue

        elapsed = _trading_days_since(last, bench)
        if elapsed < rebal_days:
            continue  # 아직 주기 미도래

        try:
            positions = portfolio_agent.load_positions(account)
        except Exception as e:
            lines.append(f"🏦 {account}: 포지션 로드 실패 — {e}")
            continue
        if not positions:
            continue

        rank_pos = {r["code"]: i for i, r in enumerate(sr["rows"])}
        buffer_rank = 3 * n_stocks

        # ── 편출: 버퍼 밖으로 밀린 보유 종목 ─────────────────────────────
        exits = [p for p in positions
                 if rank_pos.get(p.code, 10 ** 9) >= buffer_rank]
        keep_codes = {p.code for p in positions} - {p.code for p in exits}

        proceeds = 0.0
        exit_names = []
        for p in exits:
            if dry:
                exit_names.append(f"{p.name}(랭크 {rank_pos.get(p.code, '유니버스밖')})")
                continue
            try:
                sig = portfolio_agent.calc_signal(p)
                if sig.error or not sig.current_price:
                    exit_names.append(f"{p.name}(시세 실패, 보류)")
                    keep_codes.add(p.code)
                    continue
                sig.signal = "리밸런싱 편출"
                sig.signal_reason = f"랭크 {rank_pos.get(p.code, '유니버스 밖')} — 버퍼({buffer_rank}) 이탈"
                t = portfolio_agent.close_position(sig, portfolio=account, cost_rate=_COST)
                proceeds += t["sell_px"] * p.quantity
                exit_names.append(f"{p.name} {t['ret']:+.1f}%")
            except Exception as e:
                exit_names.append(f"{p.name}(실패: {e})")
                keep_codes.add(p.code)

        # ── 편입: 상위 랭킹 미보유 종목 (상관 분산) ──────────────────────
        n_new = max(0, n_stocks - len(keep_codes))
        entry_names = []
        if n_new > 0 and (dry or proceeds > 0):
            budget = (proceeds / n_new) if not dry else 0.0
            rets = {c: d.copy().assign(date=lambda x: pd.to_datetime(x["date"]))
                    .set_index("date")["close"].pct_change().tail(120)
                    for c, d in data.items()}

            def _corr(a: str, b: str) -> float:
                if a not in rets or b not in rets:
                    return 0.0
                j = pd.concat([rets[a], rets[b]], axis=1, join="inner").dropna()
                if len(j) < 60:
                    return 0.0
                c = j.corr().iloc[0, 1]
                return float(c) if pd.notna(c) else 0.0

            held = set(keep_codes)
            for r in sr["rows"]:
                if len(entry_names) >= n_new:
                    break
                c = r["code"]
                if c in held or c in {p.code for p in exits}:
                    continue
                if sum(1 for h in held if _corr(c, h) > _CORR_CAP) > 1:
                    continue
                name = meta.get(c, {}).get("name", c)
                if dry:
                    entry_names.append(name)
                    held.add(c)
                    continue
                price = float(data[c]["close"].iloc[-1])
                qty = int(budget // (price * (1 + _COST)))
                if qty < 1:
                    continue
                portfolio_agent.add_position(
                    portfolio_agent.Position(
                        code=c, name=name,
                        buy_price=round(price * (1 + _COST), 2 if market == "US" else 0),
                        quantity=qty, buy_date=today, market=market,
                    ),
                    portfolio=account,
                )
                entry_names.append(f"{name} x{qty}")
                held.add(c)

        if not dry:
            _update_last_rebal(account, today)

        tag = "[dry] " if dry else ""
        lines.append(f"🏦 *{account}* {tag}리밸런싱 ({elapsed}거래일 경과)")
        lines.append(f"   편출: {', '.join(exit_names) or '없음 (전원 버퍼 내 유지)'}")
        if entry_names:
            lines.append(f"   편입: {', '.join(entry_names)}")

    return lines

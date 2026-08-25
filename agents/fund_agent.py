# -*- coding: utf-8 -*-
"""
펀드 시뮬레이션 에이전트

과거 설정일부터 오늘까지, 스크리너(기술점수 상위 N종목)를 주기적으로 리밸런싱하며
운용했을 때의 펀드 성과를 시뮬레이션한다. 기준가 1,000원 시작.

리밸런싱마다 유니버스를 재구성한다:
  후보 풀(현재 시총 상위, 유니버스의 2배)을 로드해 두고, 각 리밸런싱 시점의
  20일 평균 거래대금 상위 n_universe 종목을 그 시점 유니버스로 삼아 채점한다.
  → 실전에서 리밸런싱 날 스크리너를 새로 돌리는 것과 동일한 로직.
  (한계: 풀 자체는 현재 상장·상위 종목 기준 — 생존 편향은 남는다)

비중 방식 (펀드 이론 근거):
  equal   : 균등 1/N — DeMiguel, Garlappi & Uppal (2009). 최적화 모델 대부분이
            추정 오차 때문에 1/N을 못 이긴다는 결과. 기본값이자 기준선.
  inv_vol : 역변동성 (Naive Risk Parity) — Qian (2005), Maillard et al. (2010).
            w_i ∝ 1/σ_i. 종목별 리스크 기여를 비슷하게 맞춘다.
  score   : 점수비례 — 기술점수(모멘텀)에 비례 배분. Jegadeesh & Titman (1993)
            모멘텀 효과 논리의 공격적 적용.

변동성 타겟팅 (선택):
  Moreira & Muir (2017, JF). KOSPI 최근 20일 실현변동성이 목표(연 15%)를 넘으면
  주식 비중을 target/realized 로 축소하고 나머지는 현금 보유.

회전율 억제 (항상 적용):
  랭크 버퍼 — 보유 종목은 점수 랭크가 3×n_top 밖으로 밀릴 때만 교체.
  노트레이드 밴드 — 보유 지속 종목의 목표-현재 차이가 NAV 1% 미만이면 매매 생략.
  (2026-08 진단: 신호의 크로스섹션 IC가 약해 잦은 교체는 비용만 발생.
   12-1 모멘텀 랭킹도 테스트했으나 이 구간에서 기술점수보다 나빠 기각.)

주의: 모든 지표(MA·RSI·MACD·OBV·볼린저)는 인과적(과거 데이터만 사용)이므로
전체 구간 점수 시계열을 한 번만 계산한 뒤 날짜로 슬라이스해도 미래 정보 누출이 없다.
"""
from __future__ import annotations

import time
from typing import Callable

import numpy as np
import pandas as pd

from . import backtest, technical_agent
from .screener import _market_leaders

ProgressCb = Callable[[int, int, str], None] | None

_FETCH_DAYS = 1200          # 최대 약 3.3년치 캘린더일
_MIN_HISTORY = 65           # 설정일 이전 필요한 최소 거래일 수
_VOL_TARGET_DEFAULT = 0.15  # 연 15%

REBALANCE_OPTIONS = {"1주": 5, "2주": 10, "4주": 20}
WEIGHT_LABELS = {
    "equal":   "균등 (1/N)",
    "inv_vol": "역변동성 (리스크 패리티)",
    "score":   "점수비례",
}

# ── 시뮬레이션 로그 (Google Sheets, "_" 접두사 = 시스템 탭) ────────────────

LOG_SHEET = "_펀드시뮬로그"
LOG_HEADER = [
    "실행일시", "설정일", "종료일", "운용일수",
    "유니버스", "편입종목", "리밸주기(일)", "비중방식", "변동성타겟",
    "누적수익률(%)", "KOSPI(%)", "초과수익(%)", "MDD(%)", "샤프",
    "평균회전율(%)", "리밸런싱횟수", "최종보유",
]


def _log_ws():
    """로그 워크시트 반환. 없으면 생성."""
    import gspread
    from .portfolio_agent import _spreadsheet
    sh = _spreadsheet()
    try:
        ws = sh.worksheet(LOG_SHEET)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=LOG_SHEET, rows=1000, cols=20)
        ws.update(values=[LOG_HEADER], range_name="A1")
        return ws
    if ws.row_values(1) != LOG_HEADER:
        ws.update(values=[LOG_HEADER], range_name="A1")
    return ws


def save_log(result: dict) -> None:
    """run() 결과 한 건을 로그 시트에 한 행으로 추가."""
    from datetime import datetime
    m = result["metrics"]
    p = result["params"]
    last_names = ""
    if result["rebalances"]:
        last_names = ", ".join(h["name"] for h in result["rebalances"][-1]["holdings"])
    row = [
        datetime.now().strftime("%Y-%m-%d %H:%M"),
        result["start_date"],
        result["end_date"],
        m["n_days"],
        p["n_universe"],
        p["n_top"],
        p["rebalance_days"],
        WEIGHT_LABELS.get(p["weighting"], p["weighting"]),
        f"{p['vol_target']:.0%}" if p.get("vol_target") else "없음",
        round(m["cum_return"] * 100, 2),
        round(m["kospi_cum"] * 100, 2) if m["kospi_cum"] is not None else "",
        round(m["excess"] * 100, 2) if m["excess"] is not None else "",
        round(m["mdd"] * 100, 2),
        round(m["sharpe"], 2) if m["sharpe"] is not None else "",
        round(m["avg_turnover"] * 100),
        m["n_rebalances"],
        last_names,
    ]
    _log_ws().append_row(row)


def load_logs() -> list[dict]:
    """저장된 로그 전체를 dict 리스트로 반환 (최신이 마지막 행)."""
    return _log_ws().get_all_records()


def _fetch_kospi(days: int = _FETCH_DAYS) -> pd.DataFrame | None:
    """KOSPI 지수 일봉. 차트 JSON API는 지수 심볼도 지원한다."""
    try:
        df = technical_agent.fetch_daily_prices_fast("KOSPI", days=days)
        return df if len(df) >= 60 else None
    except Exception:
        return None


def _weights(scheme: str, codes: list[str], scores: dict[str, float],
             sigmas: dict[str, float]) -> dict[str, float]:
    """비중 방식별 정규화 가중치 (합 = 1)."""
    if scheme == "inv_vol":
        raw = {}
        for c in codes:
            s = sigmas.get(c)
            raw[c] = 1.0 / s if s and s > 1e-6 else None
        if any(v is None for v in raw.values()):
            raw = {c: 1.0 for c in codes}  # 변동성 추정 불가 시 균등 폴백
    elif scheme == "score":
        # 점수 하한 0.05 — 상위권이어도 점수가 0 근처면 최소 비중은 유지
        raw = {c: max(scores.get(c, 0.0), 0.05) for c in codes}
    else:  # equal
        raw = {c: 1.0 for c in codes}
    total = sum(raw.values())
    return {c: v / total for c, v in raw.items()}


def run(
    start_date_str: str,
    n_universe: int = 100,
    n_top: int = 5,
    rebalance_days: int = 10,
    weighting: str = "equal",
    vol_target: float | None = None,
    cost_rate: float = 0.003,
    progress: ProgressCb = None,
) -> dict:
    """
    start_date_str : 펀드 설정일 "YYYY-MM-DD"
    n_universe     : 리밸런싱 시점마다 재구성하는 거래대금 상위 유니버스 크기
    n_top          : 편입 종목 수
    rebalance_days : 리밸런싱 주기 (거래일)
    weighting      : "equal" | "inv_vol" | "score"
    vol_target     : 연 목표 변동성 (예: 0.15). None이면 항상 100% 주식
    cost_rate      : 편도 거래비용 (수수료+세금+슬리피지)
    """
    start_ts = pd.Timestamp(start_date_str)

    # ── 1. 후보 풀 가격 데이터 로드 (유일하게 느린 구간) ──────────────────
    # 유니버스는 리밸런싱 시점마다 다시 구성하므로, 넉넉한 풀(유니버스 2배)을 로드해 둔다.
    pool_n = min(500, max(n_universe * 2, n_universe + 50))
    leaders = _market_leaders(kospi_pages=6, kosdaq_pages=4)[:pool_n]
    stocks: dict[str, dict] = {}   # code -> {close(al), score(al), ret(al), value(al), min_date}
    names: dict[str, str] = {}

    kospi_df = _fetch_kospi()

    raw: dict[str, pd.DataFrame] = {}
    for i, cand in enumerate(leaders, 1):
        if progress:
            progress(i, len(leaders), f"{cand.name}({cand.code})")
        try:
            df = technical_agent.fetch_daily_prices_fast(cand.code, days=_FETCH_DAYS)
            df = df.copy()
            df["date"] = pd.to_datetime(df["date"])
            if len(df) < _MIN_HISTORY:
                continue
            raw[cand.code] = df
            names[cand.code] = cand.name
        except Exception:
            continue
        time.sleep(0.05)

    if not raw:
        raise ValueError("가격 데이터를 가져올 수 있는 종목이 없습니다. 잠시 후 다시 시도해 주세요.")

    # ── 2. 거래일 캘린더 (KOSPI 기준, 폴백: 데이터 최다 종목) ─────────────
    if kospi_df is not None:
        cal_src = kospi_df.copy()
        cal_src["date"] = pd.to_datetime(cal_src["date"])
        all_dates = cal_src["date"]
        kospi_close_full = cal_src.set_index("date")["close"]
    else:
        longest = max(raw.values(), key=len)
        all_dates = longest["date"]
        kospi_close_full = None

    calendar = all_dates[all_dates >= start_ts].reset_index(drop=True)
    if len(calendar) < rebalance_days + 2:
        raise ValueError("설정일 이후 거래일이 너무 적습니다. 더 이전 날짜를 선택해 주세요.")

    # ── 3. 종목별 점수·가격·거래대금 시계열 사전 계산 (전부 인과 지표) ────
    for code, df in raw.items():
        enriched = technical_agent.enrich(df.copy())
        score = backtest.score_series(enriched)
        score.index = df["date"].values
        close = df.set_index("date")["close"]
        # 20일 평균 거래대금 — 리밸런싱 시점의 유니버스(유동성 상위) 재구성용
        value = (df["close"] * df["volume"]).rolling(20).mean()
        value.index = df["date"].values
        close_al = close.reindex(calendar.values).ffill()
        score_al = score.reindex(calendar.values).ffill()
        value_al = value.reindex(calendar.values).ffill()
        stocks[code] = {
            "close":    close_al,
            "score":    score_al,
            "value":    value_al,
            "ret":      close_al.pct_change(),
            # 상장 후 최소 이력(_MIN_HISTORY 거래일)이 쌓인 날짜부터 편입 가능
            "min_date": pd.Timestamp(df["date"].iloc[_MIN_HISTORY - 1]),
        }

    kospi_al = None
    kospi_ret_full = None
    if kospi_close_full is not None:
        kospi_al = kospi_close_full.reindex(calendar.values).ffill()
        kospi_ret_full = kospi_close_full.pct_change()

    # ── 4. 시뮬레이션 루프 ────────────────────────────────────────────────
    rebalance_idx = set(range(0, len(calendar), rebalance_days))
    cash = 1.0                       # NAV를 1.0에서 시작 (기준가 1,000원 = ×1000)
    shares: dict[str, float] = {}
    daily: list[dict] = []
    positions_daily: dict[str, dict] = {}
    rebalances: list[dict] = []

    for idx, t in enumerate(calendar.values):
        t = pd.Timestamp(t)
        t_str = str(t.date())

        # 평가 (당일 종가)
        nav = cash + sum(sh * stocks[c]["close"].loc[t]
                         for c, sh in shares.items()
                         if not pd.isna(stocks[c]["close"].loc[t]))

        if idx in rebalance_idx:
            # ── 유니버스 재구성: 그 시점 20일 평균 거래대금 상위 n_universe ──
            # (실전에서 그날 스크리너를 돌리는 것과 동일한 로직)
            eligible = {}
            for c, s in stocks.items():
                if s["min_date"] > t:
                    continue  # 아직 상장 이력 부족
                val = s["value"].loc[t]
                px = s["close"].loc[t]
                if not pd.isna(val) and not pd.isna(px) and px > 0:
                    eligible[c] = float(val)
            universe = sorted(eligible, key=eligible.get, reverse=True)[:n_universe]

            # ── 종목 선정: 점수 랭킹 + 버퍼 규칙 (회전율 억제) ───────────
            # 보유 종목은 랭크가 3×n_top 밖으로 밀려날 때만 교체한다.
            # (모멘텀 문헌의 rank-buffer 기법 — 신호 노이즈로 인한 불필요한 매매 방지)
            cand_scores = {}
            for c in universe:
                sc = stocks[c]["score"].loc[t]
                if not pd.isna(sc):
                    cand_scores[c] = float(sc)
            ranked = sorted(cand_scores, key=cand_scores.get, reverse=True)
            rank_pos = {c: i for i, c in enumerate(ranked)}
            buffer_rank = 3 * n_top
            kept = [c for c in shares if rank_pos.get(c, 10 ** 9) < buffer_rank]
            fresh = [c for c in ranked if c not in kept][: max(0, n_top - len(kept))]
            picked = kept + fresh

            if picked:
                # 역변동성용 σ (최근 60거래일)
                sigmas = {}
                for c in picked:
                    r = stocks[c]["ret"].loc[:t].tail(60)
                    sigmas[c] = float(r.std()) if len(r) >= 20 else None
                w = _weights(weighting, picked, cand_scores, sigmas)

                # 변동성 타겟팅: KOSPI 20일 실현변동성 기준 주식 비중 축소
                eq_frac = 1.0
                if vol_target and kospi_ret_full is not None:
                    rv = kospi_ret_full.loc[:t].tail(20).std() * np.sqrt(252)
                    if rv and rv > 1e-6:
                        eq_frac = float(min(1.0, vol_target / rv))
                w = {c: v * eq_frac for c, v in w.items()}

                # ── 체결: 목표 비중 리밸런싱 (노트레이드 밴드 + 거래비용) ─
                cur_val = {c: sh * stocks[c]["close"].loc[t] for c, sh in shares.items()}
                tgt_val = {c: w[c] * nav for c in picked}
                # 이미 보유 중이고 목표와의 차이가 NAV 1% 미만이면 매매 생략 (비용 절감)
                for c in picked:
                    cv = cur_val.get(c, 0.0)
                    if cv > 0 and abs(tgt_val[c] - cv) < 0.01 * nav:
                        tgt_val[c] = cv
                traded = sum(abs(tgt_val.get(c, 0.0) - cur_val.get(c, 0.0))
                             for c in set(cur_val) | set(tgt_val))
                cost = cost_rate * traded
                turnover = traded / nav if nav > 0 else 0.0

                nav_after = nav - cost
                # 비용은 매매가 발생한 포지션에서 차감 (밴드로 유지된 포지션은 그대로)
                adj = [c for c in picked if tgt_val[c] != cur_val.get(c, 0.0)]
                adj_sum = sum(tgt_val[c] for c in adj)
                if adj_sum > 0:
                    scale = max(0.0, 1 - cost / adj_sum)
                    for c in adj:
                        tgt_val[c] *= scale
                new_shares = {c: tgt_val[c] / stocks[c]["close"].loc[t] for c in picked}
                cash = nav_after - sum(tgt_val.values())

                prev_set = set(shares)
                entries = [names[c] for c in picked if c not in prev_set]
                exits = [names[c] for c in prev_set if c not in picked]
                shares = new_shares
                nav = nav_after

                rebalances.append({
                    "date":        t_str,
                    "holdings":    [{
                        "code":   c,
                        "name":   names[c],
                        "weight": w[c],
                        "price":  float(stocks[c]["close"].loc[t]),
                        "score":  cand_scores[c],
                    } for c in picked],
                    "entries":     entries,
                    "exits":       exits,
                    "turnover":    turnover,
                    "equity_frac": sum(w.values()),
                })

        daily.append({
            "date":  t_str,
            "nav":   float(nav),
            "kospi": float(kospi_al.loc[t]) if kospi_al is not None and not pd.isna(kospi_al.loc[t]) else None,
        })
        positions_daily[t_str] = {
            c: float(stocks[c]["close"].loc[t])
            for c in shares
            if not pd.isna(stocks[c]["close"].loc[t])
        }

    # ── 5. 성과 지표 ──────────────────────────────────────────────────────
    navs = np.array([d["nav"] for d in daily])
    rets = navs[1:] / navs[:-1] - 1
    n_days = len(navs)
    cum = float(navs[-1] / navs[0] - 1)
    cagr = float((navs[-1] / navs[0]) ** (252 / n_days) - 1) if n_days > 20 else None
    mdd = float((navs / np.maximum.accumulate(navs) - 1).min())
    sharpe = float(rets.mean() / rets.std() * np.sqrt(252)) if len(rets) > 5 and rets.std() > 0 else None

    kospi_cum = None
    kospi_vals = [d["kospi"] for d in daily if d["kospi"] is not None]
    if len(kospi_vals) >= 2:
        kospi_cum = float(kospi_vals[-1] / kospi_vals[0] - 1)

    # 첫 이벤트는 최초 설정(전액 매수)이므로 평균 회전율에서 제외
    turnovers = [r["turnover"] for r in rebalances[1:]]

    return {
        "start_date":      start_date_str,
        "end_date":        daily[-1]["date"] if daily else None,
        "params": {
            "n_universe":     n_universe,
            "n_top":          n_top,
            "rebalance_days": rebalance_days,
            "weighting":      weighting,
            "vol_target":     vol_target,
            "cost_rate":      cost_rate,
        },
        "n_scanned":       len(stocks),   # 후보 풀 크기 (유니버스는 리밸런싱마다 이 중 상위 n_universe)
        "daily":           daily,
        "positions_daily": positions_daily,
        "rebalances":      rebalances,
        "names":           names,
        "metrics": {
            "cum_return":   cum,
            "cagr":         cagr,
            "mdd":          mdd,
            "sharpe":       sharpe,
            "kospi_cum":    kospi_cum,
            "excess":       cum - kospi_cum if kospi_cum is not None else None,
            "avg_turnover": float(np.mean(turnovers)) if turnovers else 0.0,
            "n_rebalances": len(rebalances),
            "n_days":       n_days,
        },
    }

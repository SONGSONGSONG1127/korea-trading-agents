# -*- coding: utf-8 -*-
"""
펀드 시뮬레이션 에이전트

과거 설정일부터 오늘까지, 스크리너(기술점수 상위 N종목)를 주기적으로 리밸런싱하며
운용했을 때의 펀드 성과를 시뮬레이션한다. 기준가 1,000원 시작.

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
    n_universe     : 거래대금 상위 탐색 종목 수
    n_top          : 편입 종목 수
    rebalance_days : 리밸런싱 주기 (거래일)
    weighting      : "equal" | "inv_vol" | "score"
    vol_target     : 연 목표 변동성 (예: 0.15). None이면 항상 100% 주식
    cost_rate      : 편도 거래비용 (수수료+세금+슬리피지)
    """
    start_ts = pd.Timestamp(start_date_str)

    # ── 1. 유니버스 가격 데이터 로드 (유일하게 느린 구간) ────────────────
    leaders = _market_leaders(kospi_pages=6, kosdaq_pages=4)[:n_universe]
    stocks: dict[str, dict] = {}   # code -> {name, close(al), score(al), ret(al)}
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
            if (df["date"] <= start_ts).sum() < _MIN_HISTORY:
                continue
            raw[cand.code] = df
            names[cand.code] = cand.name
        except Exception:
            continue
        time.sleep(0.05)

    if not raw:
        raise ValueError("설정일 이전 데이터가 충분한 종목이 없습니다. 설정일을 조정해 주세요.")

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

    # ── 3. 종목별 점수·가격 시계열 사전 계산 (전부 인과 지표) ─────────────
    for code, df in raw.items():
        enriched = technical_agent.enrich(df.copy())
        score = backtest.score_series(enriched)
        score.index = df["date"].values
        close = df.set_index("date")["close"]
        close_al = close.reindex(calendar.values).ffill()
        score_al = score.reindex(calendar.values).ffill()
        stocks[code] = {
            "close": close_al,
            "score": score_al,
            "ret":   close_al.pct_change(),
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
            # ── 종목 선정: 당일 점수 상위 n_top ──────────────────────────
            cand_scores = {}
            for c, s in stocks.items():
                sc = s["score"].loc[t]
                px = s["close"].loc[t]
                if not pd.isna(sc) and not pd.isna(px) and px > 0:
                    cand_scores[c] = float(sc)
            picked = sorted(cand_scores, key=cand_scores.get, reverse=True)[:n_top]

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

                # ── 체결: 목표 비중으로 전면 리밸런싱 (거래비용 차감) ────
                cur_val = {c: sh * stocks[c]["close"].loc[t] for c, sh in shares.items()}
                tgt_val = {c: w[c] * nav for c in picked}
                traded = sum(abs(tgt_val.get(c, 0.0) - cur_val.get(c, 0.0))
                             for c in set(cur_val) | set(tgt_val))
                cost = cost_rate * traded
                turnover = traded / nav if nav > 0 else 0.0

                nav_after = nav - cost
                tgt_val = {c: w[c] * nav_after for c in picked}
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
        "n_scanned":       len(stocks),
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

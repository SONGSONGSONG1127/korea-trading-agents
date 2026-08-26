# -*- coding: utf-8 -*-
"""
미국 스크리너 (USA-TradingAgents)

S&P500 전 종목을 일괄 다운로드 → 거래대금($) 상위 필터 → 기술점수 랭킹.
뉴스·펀더멘탈 단계는 v1 미지원 (K-Trading의 stage1에 해당).
점수 엔진(enrich·score_series)은 K-Trading과 동일 — 시장 중립 지표라 재사용.
"""
from __future__ import annotations

from typing import Callable

import pandas as pd

from . import backtest, technical_agent, us_data

ProgressCb = Callable[[int, int, str], None] | None


def run(liquidity_top: int = 200, n_top: int = 20, progress: ProgressCb = None) -> dict:
    """
    liquidity_top : 20일 평균 거래대금($) 상위 필터 수
    n_top         : 기술점수 상위 반환 수
    """
    univ = us_data.sp500_universe()
    meta = {u["code"]: u for u in univ}
    data = us_data.fetch_many([u["code"] for u in univ], days=220, progress=progress)

    # 거래대금($) 상위
    dollar_vol = {}
    for t, df in data.items():
        if len(df) >= 70:
            dollar_vol[t] = float((df["close"] * df["volume"]).tail(20).mean())
    top_liq = sorted(dollar_vol, key=dollar_vol.get, reverse=True)[:liquidity_top]

    scored = []
    for t in top_liq:
        df = data[t]
        try:
            enriched = technical_agent.enrich(df.copy())
            q = backtest.score_series(enriched).iloc[-1]
            if pd.isna(q):
                continue
            # 다운로드해 둔 데이터로 멀티기간 백테스트까지 즉시 계산 (추가 요청 0)
            periods = backtest.run_multiperiod(enriched)
            scored.append({
                "code":    t,
                "name":    meta[t]["name"],
                "sector":  meta[t]["sector"],
                "score":   round(float(q), 3),
                "close":   float(df["close"].iloc[-1]),
                "value":   dollar_vol[t],
                "periods": periods,
                "n_days":  len(df),
            })
        except Exception:
            continue

    scored.sort(key=lambda x: -x["score"])
    return {
        "rows":      scored[:n_top],
        "n_universe": len(univ),
        "n_data":    len(data),
        "n_liq":     len(top_liq),
        "run_date":  str(pd.Timestamp.now().date()),
    }

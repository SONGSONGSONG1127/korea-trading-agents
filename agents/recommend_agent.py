# -*- coding: utf-8 -*-
"""
추천 포트폴리오 생성기 (K·USA 공통) — B1 멀티팩터 랭킹 기반

종목 선정: factors.smart_rank — IC 검증을 통과한 팩터만 가중 편입하는
컴포지트 랭킹 (tech / lowvol / resmom, 가중치 ∝ 최근 1년 IC).
비중 배분: 펀드 엔진 재사용 (균등 / 역변동성 / 점수비례).
"""
from __future__ import annotations

import time
from typing import Callable

import pandas as pd

from . import factors, fund_agent, technical_agent, us_data
from .screener import _market_leaders

ProgressCb = Callable[[int, int, str], None] | None


def build(
    market: str = "KR",
    n_stocks: int = 10,
    weighting: str = "equal",
    capital: float = 10_000_000,
    progress: ProgressCb = None,
) -> dict:
    """
    market    : "KR" | "US"
    n_stocks  : 편입 종목 수
    weighting : "equal" | "inv_vol" | "score" (score = 멀티팩터 컴포지트 비례)
    capital   : 투자금 (KR: 원, US: 달러)
    """
    # ── 1. 데이터 로드 (팩터 계산에 550거래일 이상 필요) ─────────────────
    if market == "US":
        univ = us_data.sp500_universe()
        meta = {u["code"]: {"name": u["name"], "sector": u["sector"]} for u in univ}
        data = us_data.fetch_many([u["code"] for u in univ][:300], days=560,
                                  progress=progress)
        bench = us_data.fetch_benchmark(days=560)
    else:
        leaders = _market_leaders(kospi_pages=6, kosdaq_pages=4)[:150]
        meta = {c.code: {"name": c.name, "sector": ""} for c in leaders}
        data = {}
        for i, c in enumerate(leaders, 1):
            if progress:
                progress(i, len(leaders), f"{c.name}({c.code})")
            try:
                df = technical_agent.fetch_daily_prices_fast(c.code, days=800)
                if len(df) >= 300:
                    data[c.code] = df
            except Exception:
                continue
            time.sleep(0.05)
        bench = technical_agent.fetch_daily_prices_fast("KOSPI", days=800)

    # ── 2. 멀티팩터 랭킹 (IC 검증 통과 팩터만 자동 편입) ──────────────────
    sr = factors.smart_rank(data, bench, n_top=n_stocks)
    if not sr["rows"]:
        raise ValueError("랭킹 결과가 비어 있습니다. 잠시 후 다시 시도해 주세요.")

    rows: list[dict] = []
    for r in sr["rows"]:
        m = meta.get(r["code"], {})
        rows.append({
            "code":   r["code"],
            "name":   m.get("name", r["code"]),
            "sector": m.get("sector", ""),
            "score":  float(r["composite"]),
            "tech":   r["tech"],
            "lowvol": r["lowvol"],
            "resmom": r["resmom"],
            "price":  float(r["close"]),
            "sigma":  (-r["lowvol"]) if r["lowvol"] is not None else None,
        })

    # ── 3. 비중 배분 → 수량 산출 ──────────────────────────────────────────
    codes = [r["code"] for r in rows]
    scores = {r["code"]: r["score"] for r in rows}
    sigmas = {r["code"]: r["sigma"] for r in rows}
    weights = fund_agent._weights(weighting, codes, scores, sigmas)

    invested = 0.0
    for r in rows:
        r["weight"] = weights[r["code"]]
        qty = int(capital * r["weight"] // r["price"]) if r["price"] > 0 else 0
        r["qty"] = max(qty, 0)
        r["amount"] = r["qty"] * r["price"]
        invested += r["amount"]

    return {
        "market":         market,
        "rows":           rows,
        "capital":        capital,
        "invested":       invested,
        "cash":           capital - invested,
        "weighting":      weighting,
        "factor_weights": sr["weights"],
        "factor_ics":     sr["ics"],
        "n_ranked":       sr["n_ranked"],
        "run_date":       str(pd.Timestamp.now().date()),
    }

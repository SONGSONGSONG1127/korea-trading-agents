# -*- coding: utf-8 -*-
"""
추천 포트폴리오 생성기 (K·USA 공통)

스크리너(기술점수 상위) → 비중 방식(펀드 엔진 재사용) → 투자금 기준 수량 산출.
결과는 그대로 모의투자 계좌(Google Sheets)로 저장하거나 실전 매수 참고표로 사용.
"""
from __future__ import annotations

from typing import Callable

import pandas as pd

from . import fund_agent, screener, us_screener

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
    weighting : "equal" | "inv_vol" | "score" (fund_agent와 동일)
    capital   : 투자금 (KR: 원, US: 달러)
    """
    rows: list[dict] = []

    if market == "US":
        res = us_screener.run(liquidity_top=150, n_top=n_stocks, progress=progress)
        for r in res["rows"]:
            rows.append({
                "code": r["code"], "name": r["name"], "sector": r.get("sector", ""),
                "score": float(r["score"]), "price": float(r["close"]),
                "sigma": r.get("sigma60"),
            })
    else:
        cands = screener.stage1(liquidity_top=150, progress=progress)[:n_stocks]
        for c in cands:
            sigma = None
            try:
                _s = c.df["close"].pct_change().tail(60).std()
                sigma = float(_s) if pd.notna(_s) else None
            except Exception:
                pass
            rows.append({
                "code": c.code, "name": c.name, "sector": "",
                "score": float(c.quick_score), "price": float(c.close),
                "sigma": sigma,
            })

    if not rows:
        raise ValueError("스크리너 결과가 비어 있습니다. 잠시 후 다시 시도해 주세요.")

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
        "market":    market,
        "rows":      rows,
        "capital":   capital,
        "invested":  invested,
        "cash":      capital - invested,
        "weighting": weighting,
        "run_date":  str(pd.Timestamp.now().date()),
    }

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

_CORR_CAP = 0.65     # 이 이상 동행하면 '같은 움직임 클러스터'로 간주
_MAX_PEERS = 1       # 클러스터당 최대 2종목 (기존 동행 종목 1개까지 허용)


def _diversify(ranked: list[dict], data: dict, n_stocks: int) -> tuple[list[dict], list[str]]:
    """상관 기반 분산 (B3): 랭킹 순서대로 담되, 이미 담은 종목과
    120일 수익률 상관이 _CORR_CAP을 넘는 종목이 2개 이상이면 건너뛴다.
    (반도체 5개가 상위권이어도 2개까지만 — 가짜 분산 방지)
    반환: (선정 목록, 제외된 종목명 목록)
    """
    rets: dict[str, pd.Series] = {}
    for r in ranked:
        df = data.get(r["code"])
        if df is None:
            continue
        d = df.copy()
        d["date"] = pd.to_datetime(d["date"])
        rets[r["code"]] = d.set_index("date")["close"].pct_change().tail(120)

    def _corr(a: str, b: str) -> float:
        j = pd.concat([rets[a], rets[b]], axis=1, join="inner").dropna()
        if len(j) < 60:
            return 0.0
        c = j.corr().iloc[0, 1]
        return float(c) if pd.notna(c) else 0.0

    picked: list[dict] = []
    skipped: list[str] = []
    for r in ranked:
        if len(picked) >= n_stocks:
            break
        if r["code"] not in rets:
            continue
        peers = sum(1 for p in picked if _corr(r["code"], p["code"]) > _CORR_CAP)
        if peers > _MAX_PEERS:
            skipped.append(r["code"])
            continue
        picked.append(r)
    # 후보 부족 시 제외분으로 채움
    if len(picked) < n_stocks:
        for r in ranked:
            if len(picked) >= n_stocks:
                break
            if r not in picked and r["code"] in rets:
                picked.append(r)
    return picked, skipped


def load_market_data(market: str = "KR", progress: ProgressCb = None
                     ) -> tuple[dict, pd.DataFrame, dict]:
    """팩터 계산용 유니버스 가격 데이터 + 벤치마크 + 메타. (추천·라이브펀드 공용)"""
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
    return data, bench, meta


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
    data, bench, meta = load_market_data(market, progress=progress)

    # ── 2. 멀티팩터 랭킹 (IC 검증 통과 팩터만 자동 편입) ──────────────────
    sr = factors.smart_rank(data, bench, n_top=n_stocks * 3)
    if not sr["rows"]:
        raise ValueError("랭킹 결과가 비어 있습니다. 잠시 후 다시 시도해 주세요.")

    # ── 2.5 상관 기반 분산 (B3): 같은 움직임 클러스터 최대 2종목 ─────────
    picked, corr_skipped = _diversify(sr["rows"], data, n_stocks)

    rows: list[dict] = []
    for r in picked:
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
        "corr_skipped":   corr_skipped,
        "run_date":       str(pd.Timestamp.now().date()),
    }

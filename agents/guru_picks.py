# -*- coding: utf-8 -*-
"""
🎩 귀재의 추천 — 6인의 투자 철학을 '스크리닝 조건식'으로 옮긴 종목 발굴.

작동 방식 (투명 공개): 거래대금 상위 유니버스(추천 포트폴리오와 동일 데이터)의
가격 시계열에서 모멘텀·변동성·낙폭·RSI 등을 계산하고, 각 철학에 해당하는
조건식으로 걸러 상위 3종목씩 뽑는다. 버핏(국장)만 네이버 재무 API로 PER·PBR을
추가 조회한다. 실제 인물의 추천이 아니며, 조건은 카드에 전부 공개된다.
"""
from __future__ import annotations

import time
from typing import Callable

import numpy as np
import pandas as pd

ProgressCb = Callable[[int, int, str], None] | None


def _rsi14(close: pd.Series) -> float:
    d = close.diff()
    up = d.clip(lower=0).rolling(14).mean()
    dn = (-d.clip(upper=0)).rolling(14).mean()
    rs = up / dn.replace(0, np.nan)
    v = (100 - 100 / (1 + rs)).iloc[-1]
    return float(v) if pd.notna(v) else 50.0


def _features(df: pd.DataFrame) -> dict | None:
    c = df["close"].astype(float)
    if len(c) < 260 or c.iloc[-1] <= 0:
        return None
    r = c.pct_change()
    t126 = c.tail(126)
    return {
        "price":    float(c.iloc[-1]),
        "mom_12_1": float(c.iloc[-21] / c.iloc[-252] - 1),
        "ret_6m":   float(c.iloc[-1] / c.iloc[-126] - 1),
        "ret_1y":   float(c.iloc[-1] / c.iloc[-252] - 1),
        "vol60":    float(r.tail(60).std() * np.sqrt(252)),
        "ma20":     float(c.rolling(20).mean().iloc[-1]),
        "ma60":     float(c.rolling(60).mean().iloc[-1]),
        "ma120":    float(c.rolling(120).mean().iloc[-1]),
        "off_high": float(c.iloc[-1] / c.tail(252).max() - 1),
        "reb_10d":  float(c.iloc[-1] / c.iloc[-10] - 1),
        "mdd_6m":   float((t126 / t126.cummax() - 1).min()),
        "rsi":      _rsi14(c),
    }


def _kr_value_scan(codes: list[str], progress: ProgressCb = None) -> dict[str, dict]:
    """버핏(국장)용 — 네이버 integration API로 PER·PBR·배당 조회."""
    from .fundamental_agent import _fetch_main
    out = {}
    for i, code in enumerate(codes, 1):
        if progress:
            progress(i, len(codes), f"재무 조회 {code}")
        try:
            m = _fetch_main(code)
            if m.get("per") and m.get("pbr"):
                out[code] = m
        except Exception:
            pass
        time.sleep(0.04)
    return out


def run(market: str = "KR", progress: ProgressCb = None) -> dict:
    from . import recommend_agent

    data, bench, meta = recommend_agent.load_market_data(market, progress=progress)
    feats: dict[str, dict] = {}
    for code, df in data.items():
        f = _features(df)
        if f:
            feats[code] = f

    def _nm(c):
        return meta.get(c, {}).get("name", c)

    def _pct(v):
        return f"{v * 100:+.1f}%"

    gurus: list[dict] = []

    # 📉 제시 리버모어 — 추세 추종
    pool = [(c, f) for c, f in feats.items()
            if f["mom_12_1"] > 0 and f["price"] > f["ma20"] > f["ma60"]]
    pool.sort(key=lambda x: -x[1]["mom_12_1"])
    gurus.append({
        "guru": "제시 리버모어", "icon": "📉", "style": "추세 추종",
        "rule": "12-1개월 수익률 상위 + 현재가 > 20일선 > 60일선 (추세 정배열)",
        "one": "추세가 살아있는 종목만. 꺾인 칼은 잡지 않습니다.",
        "picks": [{"code": c, "name": _nm(c),
                   "why": f"12-1M 모멘텀 {_pct(f['mom_12_1'])} · 정배열 유지"}
                  for c, f in pool[:3]],
    })

    # 🔍 피터 린치 — 꾸준한 성장
    pool = [(c, f) for c, f in feats.items()
            if f["ret_6m"] > 0 and f["ret_1y"] > 0 and f["mdd_6m"] > -0.25]
    pool.sort(key=lambda x: -(x[1]["ret_1y"] / max(x[1]["vol60"], 0.05)))
    gurus.append({
        "guru": "피터 린치", "icon": "🔍", "style": "꾸준한 성장",
        "rule": "6개월·1년 수익 모두 플러스 + 6개월 최대낙폭 −25% 이내 → 수익/변동성 비율 상위",
        "one": "요란하지 않게 꾸준히 오르는 종목 — 10루타의 재료입니다.",
        "picks": [{"code": c, "name": _nm(c),
                   "why": f"1년 {_pct(f['ret_1y'])} · 낙폭 {_pct(f['mdd_6m'])} 완만"}
                  for c, f in pool[:3]],
    })

    # 🎭 코스톨라니 — 낙폭과대 역발상
    pool = [(c, f) for c, f in feats.items()
            if f["off_high"] <= -0.30 and f["reb_10d"] >= 0.03]
    pool.sort(key=lambda x: x[1]["off_high"])
    gurus.append({
        "guru": "앙드레 코스톨라니", "icon": "🎭", "style": "낙폭과대 역발상",
        "rule": "52주 고점 대비 −30% 이상 하락 + 최근 10일 +3% 이상 반등 시작",
        "one": "부화뇌동파가 던진 주식이 바닥을 다지기 시작한 자리입니다.",
        "picks": [{"code": c, "name": _nm(c),
                   "why": f"고점 대비 {_pct(f['off_high'])} · 10일 반등 {_pct(f['reb_10d'])}"}
                  for c, f in pool[:3]],
    })

    # ⚖️ 레이 달리오 — 저변동 안정
    pool = [(c, f) for c, f in feats.items() if f["ret_1y"] > 0]
    pool.sort(key=lambda x: x[1]["vol60"])
    gurus.append({
        "guru": "레이 달리오", "icon": "⚖️", "style": "저변동 안정",
        "rule": "1년 수익 플러스 중 60일 변동성이 가장 낮은 종목 (저변동성 이상현상)",
        "one": "덜 흔들리면서 우상향 — 포트폴리오의 닻이 되는 종목입니다.",
        "picks": [{"code": c, "name": _nm(c),
                   "why": f"연변동성 {f['vol60'] * 100:.0f}% · 1년 {_pct(f['ret_1y'])}"}
                  for c, f in pool[:3]],
    })

    # 🌊 하워드 막스 — 공포 구간 매수
    pool = [(c, f) for c, f in feats.items()
            if f["rsi"] < 40 and f["price"] > f["ma120"]]
    pool.sort(key=lambda x: x[1]["rsi"])
    gurus.append({
        "guru": "하워드 막스", "icon": "🌊", "style": "공포 구간 매수",
        "rule": "RSI 40 미만(단기 공포) + 현재가 > 120일선(장기 추세는 유지)",
        "one": "장기 추세는 살아있는데 단기 공포에 눌린 종목 — 사이클의 선물입니다.",
        "picks": [{"code": c, "name": _nm(c),
                   "why": f"RSI {f['rsi']:.0f} · 장기 추세 위"}
                  for c, f in pool[:3]],
    })

    # 🏛️ 워런 버핏 — 가치·우량
    if market == "KR":
        cand = [c for c in list(data.keys())[:60] if c in feats]
        vals = _kr_value_scan(cand, progress=progress)
        pers = sorted(v["per"] for v in vals.values() if v.get("per") and v["per"] > 0)
        med = pers[len(pers) // 2] if pers else 12.0
        pool2 = [(c, vals[c]) for c in vals
                 if 0 < vals[c]["per"] < med and 0 < vals[c]["pbr"] < 1.5]
        pool2.sort(key=lambda x: x[1]["per"] * x[1]["pbr"])
        rule = (f"PER이 유니버스 중앙값({med:.1f}배)보다 낮고 PBR < 1.5 → PER×PBR 낮은 순 "
                "(그레이엄·버핏식 가치 필터, 네이버 재무 API 실조회)")
        picks = [{"code": c, "name": _nm(c),
                  "why": f"PER {v['per']:.1f}배 · PBR {v['pbr']:.2f}배"
                         + (f" · 배당 {v['div_yield']:.1f}%" if v.get("div_yield") else "")}
                 for c, v in pool2[:3]]
    else:
        pool = [(c, f) for c, f in feats.items()
                if f["ret_1y"] > 0.10 and f["mdd_6m"] > -0.20]
        med_vol = float(np.median([f["vol60"] for f in feats.values()])) if feats else 0.3
        pool = [(c, f) for c, f in pool if f["vol60"] < med_vol]
        pool.sort(key=lambda x: -x[1]["ret_1y"])
        rule = "1년 +10% 이상 + 낙폭 −20% 이내 + 변동성 하위 50% (우량 복리 성장 근사 — 가격 기반)"
        picks = [{"code": c, "name": _nm(c),
                  "why": f"1년 {_pct(f['ret_1y'])} · 낙폭 {_pct(f['mdd_6m'])}"}
                 for c, f in pool[:3]]
    gurus.insert(0, {
        "guru": "워런 버핏", "icon": "🏛️", "style": "가치·우량",
        "rule": rule,
        "one": "싸고 탄탄한 기업 — 가격이 가치보다 낮을 때만 움직입니다.",
        "picks": picks,
    })

    return {"market": market, "gurus": gurus, "n_universe": len(feats),
            "run_date": str(pd.Timestamp.now().date())}

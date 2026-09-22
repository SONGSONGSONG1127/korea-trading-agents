# -*- coding: utf-8 -*-
"""
레짐 스위처 (B2) — 지금이 어떤 장세이고, 어떤 전략을 쓸 때인지

3축 판정:
  추세   : 지수 vs 60일선 (순풍/중립/역풍)
  시장 폭 : 소형/동일가중 vs 대형 지수의 60일 상대강도
           KR: KOSDAQ vs KOSPI · US: RSP(S&P500 동일가중) vs S&P500
           — "지수만 오르는 쏠림장에선 개별 픽이 지수를 못 이긴다"는
             1년 백테스트의 교훈을 지표화한 것
  변동성 : 지수 20일 실현변동성 연율화 (저 <15% < 중 < 25% < 고)

출력: 각 축 판정 + 전략 조언 문장 (스크리너/추천/브리핑에 표시)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

_BREADTH_WIN = 60   # 상대강도 측정 거래일
_BREADTH_TH = 0.02  # ±2%p


def _trend(close: pd.Series) -> tuple[str, str]:
    ma60 = close.rolling(60).mean().iloc[-1]
    last = float(close.iloc[-1])
    if last > ma60 * 1.005:
        return "순풍", f"지수 {last:,.0f} > 60일선 {ma60:,.0f}"
    if last < ma60 * 0.995:
        return "역풍", f"지수 {last:,.0f} < 60일선 {ma60:,.0f}"
    return "중립", f"지수 {last:,.0f} ≈ 60일선 {ma60:,.0f}"


def analyze(market: str = "KR") -> dict:
    from . import technical_agent, us_data

    if market == "US":
        idx = us_data.fetch_benchmark(days=300)
        small = us_data.fetch_daily_prices("RSP", days=300)
        idx_name, small_name = "S&P500", "동일가중(RSP)"
    else:
        idx = technical_agent.fetch_daily_prices_fast("KOSPI", days=300)
        small = technical_agent.fetch_daily_prices_fast("KOSDAQ", days=300)
        idx_name, small_name = "KOSPI", "KOSDAQ"

    close = idx["close"].reset_index(drop=True)
    trend, trend_ev = _trend(close)

    # 변동성
    rv = float(close.pct_change().tail(20).std() * np.sqrt(252))
    vol_label = "저" if rv < 0.15 else ("중" if rv < 0.25 else "고")

    # 시장 폭
    sc = small["close"].reset_index(drop=True)
    breadth_spread = None
    breadth = "판정불가"
    if len(sc) > _BREADTH_WIN and len(close) > _BREADTH_WIN:
        r_small = float(sc.iloc[-1] / sc.iloc[-1 - _BREADTH_WIN] - 1)
        r_idx = float(close.iloc[-1] / close.iloc[-1 - _BREADTH_WIN] - 1)
        breadth_spread = r_small - r_idx
        breadth = ("폭 넓음" if breadth_spread > _BREADTH_TH
                   else ("쏠림" if breadth_spread < -_BREADTH_TH else "중립"))

    # ── 전략 조언 매트릭스 ────────────────────────────────────────────────
    if trend == "순풍" and breadth == "쏠림":
        advice = (f"대형주 주도 강세장 — {idx_name}(코어/ETF)이 개별 픽보다 유리한 국면. "
                  "스크리너 픽은 소액 새틀라이트로만, 초과수익 기대치는 낮출 것.")
    elif trend == "순풍" and breadth == "폭 넓음":
        advice = "순환매 강세장 — 시장의 폭이 넓어 스크리너·모멘텀 픽이 유리한 국면."
    elif trend == "순풍":
        advice = "완만한 강세 — 표준 운용. 신호 규율 유지."
    elif vol_label == "고":
        advice = ("불안정 국면 — 변동성 타겟팅 ON 권장, 신규 진입 축소·현금 비중 확대. "
                  "급락 후 V반등이 잦으니 개별 종목 패닉 손절은 자제(볼타겟이 방어 담당).")
    elif trend == "역풍":
        advice = "하락 추세 — 손절 규율 우선. 할인찬스 후보는 관찰만(낙하는 칼 주의)."
    else:
        advice = "횡보 국면 — 모멘텀 불리, 가치(할인찬스) 전략이 상대적으로 유리."
    if trend == "순풍" and vol_label == "고":
        advice += " 변동성이 높은 랠리 — 볼타겟을 켜면 낙폭 방어에 유리."

    return {
        "market":         market,
        "trend":          trend,
        "trend_ev":       trend_ev,
        "vol":            rv,
        "vol_label":      vol_label,
        "breadth":        breadth,
        "breadth_spread": breadth_spread,
        "idx_name":       idx_name,
        "small_name":     small_name,
        "advice":         advice,
        "asof":           str(pd.Timestamp.now().date()),
    }


def summary_line(r: dict) -> str:
    """한 줄 요약 (브리핑·배너용)."""
    b = (f"{r['small_name']} {r['breadth_spread']:+.1%}p" if r["breadth_spread"] is not None else "—")
    return (f"{r['idx_name']} {r['trend']} · 변동성 {r['vol_label']}({r['vol']:.0%}) · "
            f"시장폭 {r['breadth']}({b})")

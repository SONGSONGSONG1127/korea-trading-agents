# -*- coding: utf-8 -*-
"""
백데이터 검증 에이전트

특정 과거 날짜에 이 시스템의 스크리너를 돌렸다면 어떤 종목이 나왔을지 시뮬레이션하고,
그 종목들이 이후 실제로 어떤 수익률을 기록했는지 확인한다.

흐름:
  1. Naver 시총 상위 종목 리스트 (현재 기준 — 생존 편향 존재, 참고용)
  2. 각 종목의 2.5년치 일봉 로드
  3. target_date 이전 데이터로 기술 점수 계산
  4. 점수 내림차순 정렬 → 상위 n_top 선정
  5. target_date 이후 실제 종가로 각 기간별 수익률 계산
"""
from __future__ import annotations

import time
from typing import Callable

import numpy as np
import pandas as pd

from . import backtest, technical_agent
from .screener import _market_leaders

ProgressCb = Callable[[int, int, str], None] | None

PERIODS = [("1주", 5), ("2주", 10), ("3주", 15), ("4주", 20), ("100일", 100), ("1년", 252)]
PERIOD_LABELS = [label for label, _ in PERIODS]


def run(
    target_date_str: str,
    n_universe: int = 100,
    n_top: int = 10,
    progress: ProgressCb = None,
) -> dict:
    """
    target_date_str  : "YYYY-MM-DD" 형식의 과거 날짜
    n_universe       : Naver 거래대금 상위에서 검색할 종목 수
    n_top            : 기술 점수 상위 반환 수
    progress(i,t,name): 진행 콜백

    반환 dict:
        target_date, n_scanned, n_top, top(list[dict]), summary(dict), period_labels
    """
    target_date = pd.Timestamp(target_date_str)

    leaders = _market_leaders()[:n_universe]
    candidates: list[dict] = []

    for i, cand in enumerate(leaders, 1):
        if progress:
            progress(i, len(leaders), f"{cand.name}({cand.code})")
        try:
            # 2.5년치 전체 일봉 (과거 + 미래 포함)
            df = technical_agent.fetch_daily_prices_fast(cand.code, days=900)
            df = df.copy()
            df["date"] = pd.to_datetime(df["date"])

            # target_date 이전 데이터로 기술 점수 계산
            df_past = df[df["date"] <= target_date].reset_index(drop=True)
            if len(df_past) < 65:   # ma60 + 여유
                continue

            enriched = technical_agent.enrich(df_past.copy())
            score = backtest.score_series(enriched).iloc[-1]
            if pd.isna(score):
                continue

            entry_price = float(df_past["close"].iloc[-1])

            # target_date 이후 실제 수익률
            df_future = df[df["date"] > target_date].reset_index(drop=True)
            returns: dict[str, float | None] = {}
            for label, period in PERIODS:
                if len(df_future) >= period:
                    exit_price = float(df_future["close"].iloc[period - 1])
                    returns[label] = (exit_price - entry_price) / entry_price
                else:
                    returns[label] = None   # 아직 미래 (데이터 없음)

            candidates.append({
                "code":        cand.code,
                "name":        cand.name,
                "score":       float(score),
                "entry_price": entry_price,
                "returns":     returns,
            })
        except Exception:
            continue
        time.sleep(0.05)

    # 점수 내림차순 → 상위 n_top
    candidates.sort(key=lambda x: x["score"], reverse=True)
    top = candidates[:n_top]

    # 전체 스캔 종목 기준 벤치마크 (전 종목 단순 평균)
    benchmark: dict[str, float | None] = {}
    for label in PERIOD_LABELS:
        vals = [c["returns"][label] for c in candidates if c["returns"].get(label) is not None]
        benchmark[label] = float(np.mean(vals)) if vals else None

    # 상위 종목 기간별 요약
    summary: dict[str, dict] = {}
    for label in PERIOD_LABELS:
        vals = [c["returns"][label] for c in top if c["returns"].get(label) is not None]
        if vals:
            summary[label] = {
                "avg":      float(np.mean(vals)),
                "win_rate": float(sum(r > 0 for r in vals) / len(vals)),
                "max":      float(max(vals)),
                "min":      float(min(vals)),
                "n":        len(vals),
            }

    return {
        "target_date":   target_date_str,
        "n_scanned":     len(candidates),
        "n_universe":    n_universe,
        "n_top":         n_top,
        "top":           top,
        "summary":       summary,
        "benchmark":     benchmark,
        "period_labels": PERIOD_LABELS,
    }

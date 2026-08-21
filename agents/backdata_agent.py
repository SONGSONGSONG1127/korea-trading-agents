# -*- coding: utf-8 -*-
"""
백데이터 검증 에이전트

특정 과거 날짜에 이 시스템의 스크리너를 돌렸다면 어떤 종목이 나왔을지 시뮬레이션하고,
그 종목들이 이후 실제로 어떤 수익률을 기록했는지 확인한다.

흐름:
  1. Naver 시총 상위 종목 리스트 (현재 기준 — 생존 편향 존재, 참고용)
  2. 각 종목의 최대 3.3년치 일봉 로드 (days=1200)
  3. target_date 이전 데이터로 기술 점수 계산
  4. 점수 내림차순 정렬 → 상위 n_top 선정
  5. target_date 이후 실제 종가로 1주/2주/3주/4주/100일/1년 수익률 계산
  6. (옵션) 해당 시점 DART 사업보고서로 펀더멘탈 보완

DART 연도 매핑:
  사업보고서는 4월 이후 전년도 기준으로 제출됨
  target_date.month >= 4 → year = target_date.year - 1
  target_date.month < 4  → year = target_date.year - 2
"""
from __future__ import annotations

import time
from typing import Callable

import numpy as np
import pandas as pd

from . import backtest, technical_agent
from .screener import _market_leaders

ProgressCb = Callable[[int, int, str], None] | None

# 최대 약 3.3년치 일봉 (1200 캘린더일 ÷ 1.42 ≈ 845 거래일)
_FETCH_DAYS = 1200

PERIODS = [("1주", 5), ("2주", 10), ("3주", 15), ("4주", 20), ("100일", 100), ("1년", 252)]
PERIOD_LABELS = [label for label, _ in PERIODS]


def _dart_year_for(target_date: pd.Timestamp) -> int:
    """target_date 기준 사업보고서 연도 반환."""
    return target_date.year - 1 if target_date.month >= 4 else target_date.year - 2


def run(
    target_date_str: str,
    n_universe: int = 100,
    n_top: int = 10,
    include_dart: bool = False,
    progress: ProgressCb = None,
) -> dict:
    """
    target_date_str : "YYYY-MM-DD" 형식의 과거 날짜
    n_universe      : 거래대금 상위 탐색 종목 수 (최대 약 500)
    n_top           : 기술점수 상위 반환 수
    include_dart    : True이면 상위 종목에 DART 펀더멘탈 추가
    progress        : (i, total, label) 콜백
    """
    target_date = pd.Timestamp(target_date_str)

    # 더 많은 시총 상위 종목 커버 (코스피 6p + 코스닥 4p = 최대 ~500종목)
    leaders = _market_leaders(kospi_pages=6, kosdaq_pages=4)[:n_universe]
    candidates: list[dict] = []

    for i, cand in enumerate(leaders, 1):
        if progress:
            progress(i, len(leaders), f"{cand.name}({cand.code})")
        try:
            df = technical_agent.fetch_daily_prices_fast(cand.code, days=_FETCH_DAYS)
            df = df.copy()
            df["date"] = pd.to_datetime(df["date"])

            df_past = df[df["date"] <= target_date].reset_index(drop=True)
            if len(df_past) < 65:
                continue

            enriched = technical_agent.enrich(df_past.copy())
            score = backtest.score_series(enriched).iloc[-1]
            if pd.isna(score):
                continue

            entry_price = float(df_past["close"].iloc[-1])
            df_future = df[df["date"] > target_date].reset_index(drop=True)

            returns: dict[str, float | None] = {}
            for label, period in PERIODS:
                if len(df_future) >= period:
                    exit_price = float(df_future["close"].iloc[period - 1])
                    returns[label] = (exit_price - entry_price) / entry_price
                else:
                    returns[label] = None

            candidates.append({
                "code":        cand.code,
                "name":        cand.name,
                "score":       float(score),
                "entry_price": entry_price,
                "returns":     returns,
                "dart":        None,
            })
        except Exception:
            continue
        time.sleep(0.05)

    candidates.sort(key=lambda x: x["score"], reverse=True)
    top = candidates[:n_top]

    # ── DART 펀더멘탈 (상위 종목만) ────────────────────────────────────────
    if include_dart and top:
        from . import dart_agent
        dart_year = _dart_year_for(target_date)
        for c in top:
            try:
                dart = dart_agent.get_financials(c["code"], year=dart_year)
                if dart:
                    c["dart"] = {
                        "year":           dart_year,
                        "f_score":        dart.f_score,
                        "f_details":      dart.f_details,
                        "roa":            dart.roa,
                        "debt_ratio":     dart.debt_ratio,
                        "op_margin":      dart.op_margin,
                        "revenue_growth": dart.revenue_growth,
                        "op_profit_growth": dart.op_profit_growth,
                    }
            except Exception:
                pass
            time.sleep(0.1)

    # ── 벤치마크 (전 스캔 종목 단순 평균) ──────────────────────────────────
    benchmark: dict[str, float | None] = {}
    for label in PERIOD_LABELS:
        vals = [c["returns"][label] for c in candidates if c["returns"].get(label) is not None]
        benchmark[label] = float(np.mean(vals)) if vals else None

    # ── 기간별 요약 통계 ────────────────────────────────────────────────────
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
        "dart_year":     _dart_year_for(target_date) if include_dart else None,
        "n_scanned":     len(candidates),
        "n_universe":    n_universe,
        "n_top":         n_top,
        "include_dart":  include_dart,
        "top":           top,
        "summary":       summary,
        "benchmark":     benchmark,
        "period_labels": PERIOD_LABELS,
    }

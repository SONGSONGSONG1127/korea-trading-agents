# -*- coding: utf-8 -*-
"""
간이 백테스트: 현재 점수 체계를 근사한 점수 시계열을 과거 전 구간에 벡터 연산으로 계산해
"종합점수 ≥ 임계" 신호의 10일 후 승률/평균 수익률을 확인한다.

※ 문맥형 RSI·합의 배수 등 일부 규칙은 근사(단순화)되어 있으며,
   표본이 적으므로 참고용 통계다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

THRESHOLD = 0.30  # 풀드 백테스트 보정값 (calibrate.py 참고)
HORIZON = 10  # 거래일


def score_series(df: pd.DataFrame) -> pd.Series:
    """enrich()된 df에서 근사 기술 점수 시계열을 만든다 (전부 인과적 지표)."""
    c, o = df["close"], df["open"]
    slope = df["ma20_slope"]

    trend = (
        np.where(c > df["ma20"], np.where(slope < 0, 0.1, 0.3), -0.3)
        + 0.15 * np.sign(df["ma5"] - df["ma20"])
        + np.select([slope > 0.005, slope < -0.005], [0.15, -0.15], 0.0)
        + 0.10 * np.sign(df["macd"])
    )

    rsi = df["rsi"]
    hist_up = (df["macd_hist"].diff() > 0).rolling(3).sum() == 3
    hist_dn = (df["macd_hist"].diff() < 0).rolling(3).sum() == 3
    momentum = (
        np.select([rsi >= 70, rsi <= 30], [-0.3, 0.3], 0.0)
        + np.select([hist_up, hist_dn], [0.15, -0.15], 0.0)
    )

    rvol = df["rvol"]
    volume = (
        np.select([rvol >= 2.0, rvol >= 1.5], [0.30, 0.15], 0.0) * np.where(c > o, 1, -1)
        + 0.15 * np.sign(df["obv"] - df["obv_ma20"])
    )

    pct_b = df["pct_b"]
    position = (
        0.20 * ((slope > 0) & pct_b.between(0.1, 0.35)).astype(float)
        - 0.20 * ((pct_b > 1.0) & (rvol < 1.5)).astype(float)
        + 0.15 * ((pct_b > 0.8) & (rvol >= 2.0)).astype(float)
    )

    score = (
        0.35 * np.clip(trend, -1, 1)
        + 0.25 * np.clip(momentum, -1, 1)
        + 0.25 * np.clip(volume, -1, 1)
        + 0.15 * np.clip(position, -1, 1)
    )
    return pd.Series(np.clip(score, -1, 1), index=df.index)


def run(df: pd.DataFrame, threshold: float = THRESHOLD, horizon: int = HORIZON) -> dict:
    scores = score_series(df)
    fwd = df["close"].shift(-horizon) / df["close"] - 1
    valid = scores.notna() & fwd.notna() & df["ma60"].notna()
    sig = valid & (scores >= threshold)

    n = int(sig.sum())
    result = {
        "threshold": threshold,
        "horizon": horizon,
        "n_signals": n,
        "n_days": int(valid.sum()),
        "win_rate": None,
        "avg_return": None,
        "base_avg_return": round(float(fwd[valid].mean()), 4) if valid.any() else None,
    }
    if n > 0:
        result["win_rate"] = round(float((fwd[sig] > 0).mean()), 3)
        result["avg_return"] = round(float(fwd[sig].mean()), 4)
    return result

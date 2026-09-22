# -*- coding: utf-8 -*-
"""
멀티팩터 랭킹 엔진 (B1 — 똑똑한 애널리스트의 핵심)

크로스섹션 팩터 3종 (모두 가격 기반 → 과거 IC 검증 가능):
  tech   : 기존 기술점수 (score_series) — 단기 상태
  lowvol : 저변동성 (−60일 수익률 표준편차) — 한국·글로벌에서 가장 강건한
           이상현상 (Blitz & van Vliet 2007; Baker et al. 2011)
  resmom : 잔차 모멘텀 — 12−1개월 수익에서 시장 베타 성분을 제거하고
           잔차 변동성으로 표준화 (Blitz, Huij & Martens 2011).
           원시 12−1 모멘텀보다 모멘텀 크래시에 강함.

원칙:
  · 팩터는 최근 IC(순위상관)가 양(+)일 때만 랭킹에 편입 — 가중치 ∝ max(IC, 0)
  · 전 팩터 IC ≤ 0이면 lowvol 단독 폴백 (문헌상 최강건 팩터)
  · 컴포지트 = Σ w_i × zscore(factor_i)  (윈저라이즈 ±3σ)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

FACTOR_NAMES = ["tech", "lowvol", "resmom"]
_MOM_WIN = 231     # 12−1개월: t−252 ~ t−21 (거래일)
_MOM_GAP = 21
_VOL_WIN = 60
_IC_HORIZON = 10   # IC 측정 보유기간 (거래일)
_IC_POINTS = 24    # 최근 몇 개 시점으로 IC 추정할지 (~1년)


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    ra = pd.Series(a).rank().values
    rb = pd.Series(b).rank().values
    if ra.std() == 0 or rb.std() == 0:
        return 0.0
    return float(np.corrcoef(ra, rb)[0, 1])


def factor_values(close: pd.Series, score: pd.Series,
                  bench_ret: pd.Series, pos: int) -> dict[str, float | None]:
    """한 종목의 위치 pos(정수 인덱스, 공통 캘린더 기준)에서의 팩터 값들.

    close/score는 공통 캘린더로 정렬(ffill)된 시리즈, bench_ret은 벤치마크 일수익률.
    전부 pos 이전 데이터만 사용(인과적).
    """
    out: dict[str, float | None] = {"tech": None, "lowvol": None, "resmom": None}

    sc = score.iloc[pos]
    if pd.notna(sc):
        out["tech"] = float(sc)

    ret = close.pct_change()
    r_win = ret.iloc[max(0, pos - _VOL_WIN + 1): pos + 1].dropna()
    if len(r_win) >= 40:
        sd = float(r_win.std())
        if sd > 0:
            out["lowvol"] = -sd

    lo, hi = pos - _MOM_WIN - _MOM_GAP + 1, pos - _MOM_GAP + 1
    if lo >= 1:
        rs = ret.iloc[lo:hi]
        rm = bench_ret.iloc[lo:hi]
        mask = rs.notna() & rm.notna()
        if mask.sum() >= 120:
            rs_v, rm_v = rs[mask].values, rm[mask].values
            var_m = rm_v.var()
            beta = float(np.cov(rs_v, rm_v)[0, 1] / var_m) if var_m > 0 else 0.0
            resid = rs_v - beta * rm_v
            rsd = resid.std()
            if rsd > 0:
                out["resmom"] = float(resid.sum() / (rsd * np.sqrt(len(resid))))
    return out


def _zscore(vals: dict[str, float]) -> dict[str, float]:
    arr = np.array(list(vals.values()), dtype=float)
    mu, sd = arr.mean(), arr.std()
    if sd == 0:
        return {k: 0.0 for k in vals}
    return {k: float(np.clip((v - mu) / sd, -3, 3)) for k, v in vals.items()}


def ic_weights(aligned: dict[str, dict[str, pd.Series]],
               bench_ret: pd.Series, n_cal: int) -> tuple[dict[str, float], dict[str, float]]:
    """최근 _IC_POINTS개 시점의 팩터별 평균 IC → 가중치.

    aligned: code -> {"close": Series, "score": Series} (공통 캘린더 정렬)
    반환: (weights, mean_ics)
    """
    ics: dict[str, list[float]] = {f: [] for f in FACTOR_NAMES}
    positions = range(n_cal - 1 - _IC_HORIZON,
                      max(_MOM_WIN + _MOM_GAP, n_cal - 1 - _IC_HORIZON * _IC_POINTS),
                      -_IC_HORIZON)
    for pos in positions:
        fac_rows, fwd = {f: {} for f in FACTOR_NAMES}, {}
        for c, s in aligned.items():
            p0, p1 = s["close"].iloc[pos], s["close"].iloc[pos + _IC_HORIZON]
            if pd.isna(p0) or pd.isna(p1) or p0 <= 0:
                continue
            fv = factor_values(s["close"], s["score"], bench_ret, pos)
            fwd[c] = p1 / p0 - 1
            for f in FACTOR_NAMES:
                if fv[f] is not None:
                    fac_rows[f][c] = fv[f]
        for f in FACTOR_NAMES:
            common = [c for c in fac_rows[f] if c in fwd]
            if len(common) >= 30:
                ics[f].append(_spearman(
                    np.array([fac_rows[f][c] for c in common]),
                    np.array([fwd[c] for c in common]),
                ))

    mean_ics = {f: (float(np.mean(v)) if v else 0.0) for f, v in ics.items()}
    raw = {f: max(ic, 0.0) for f, ic in mean_ics.items()}
    total = sum(raw.values())
    if total <= 1e-9:
        weights = {"tech": 0.0, "lowvol": 1.0, "resmom": 0.0}  # 폴백: 저변동성 단독
    else:
        weights = {f: v / total for f, v in raw.items()}
    return weights, mean_ics


def smart_rank(data: dict[str, pd.DataFrame], bench_df: pd.DataFrame,
               n_top: int = 20) -> dict:
    """배치 로드된 가격 데이터 → 멀티팩터 컴포지트 랭킹.

    data: code -> df(date/ohlcv, 550거래일 이상 권장)
    반환: {rows: [{code, composite, tech, lowvol, resmom, close}], weights, ics, n_ranked}
    """
    from . import backtest, technical_agent

    bench = bench_df.copy()
    bench["date"] = pd.to_datetime(bench["date"])
    cal = bench["date"].reset_index(drop=True)
    bench_close = bench.set_index("date")["close"].reindex(cal.values).ffill()
    bench_ret = bench_close.pct_change()
    n_cal = len(cal)

    aligned: dict[str, dict[str, pd.Series]] = {}
    for c, df in data.items():
        if len(df) < 100:
            continue
        d = df.copy()
        d["date"] = pd.to_datetime(d["date"])
        enr = technical_agent.enrich(d.copy())
        sc = backtest.score_series(enr)
        sc.index = d["date"].values
        close = d.set_index("date")["close"]
        aligned[c] = {
            "close": close.reindex(cal.values).ffill(),
            "score": sc.reindex(cal.values).ffill(),
        }

    weights, ics = ic_weights(aligned, bench_ret, n_cal)

    # 최신 시점 팩터 → z-score → 컴포지트
    latest: dict[str, dict[str, float]] = {}
    for c, s in aligned.items():
        fv = factor_values(s["close"], s["score"], bench_ret, n_cal - 1)
        if all(fv[f] is None for f in FACTOR_NAMES):
            continue
        latest[c] = fv

    z_by_factor: dict[str, dict[str, float]] = {}
    for f in FACTOR_NAMES:
        vals = {c: fv[f] for c, fv in latest.items() if fv[f] is not None}
        z_by_factor[f] = _zscore(vals) if len(vals) >= 5 else {}

    rows = []
    for c, fv in latest.items():
        comp, wsum = 0.0, 0.0
        for f in FACTOR_NAMES:
            z = z_by_factor[f].get(c)
            if z is not None and weights[f] > 0:
                comp += weights[f] * z
                wsum += weights[f]
        if wsum <= 0:
            continue
        rows.append({
            "code":      c,
            "composite": comp / wsum,
            "tech":      fv["tech"],
            "lowvol":    fv["lowvol"],
            "resmom":    fv["resmom"],
            "close":     float(aligned[c]["close"].iloc[-1]),
        })
    rows.sort(key=lambda x: -x["composite"])
    return {"rows": rows[:n_top], "weights": weights, "ics": ics, "n_ranked": len(rows)}

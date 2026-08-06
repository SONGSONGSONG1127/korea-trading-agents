# -*- coding: utf-8 -*-
"""
100점 만점 투자 매력 점수

원점수(기술 종합, -1~+1)를 임의 환산하지 않고,
풀드 백테스트(calibrate.py)로 만든 과거 분포의 '백분위'로 변환한다.
→ "점수 83점 = 과거 4만 종목-일 표본 중 상위 17% 신호"라는 검증 가능한 의미를 가진다.

calibration.json이 없으면 예외를 던진다 (임의 점수를 만들지 않기 위해 폴백 없음).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

_PATH = Path(__file__).parent / "calibration.json"
_CAL: dict | None = None


def _cal() -> dict:
    global _CAL
    if _CAL is None:
        _CAL = json.loads(_PATH.read_text(encoding="utf-8"))
    return _CAL


def meta() -> dict:
    c = _cal()
    return {k: c[k] for k in
            ("generated", "n_samples", "n_stocks", "horizon", "baseline_win", "baseline_avg")}


def to_score100(raw: float) -> int:
    """원점수 → 과거 분포 백분위 (1~99). 분포 밖 값은 경계로 클램프."""
    q = _cal()["quantiles"]
    ps, vals = [], []
    for p in range(1, 100):
        v = q[str(p)]
        if not vals or v > vals[-1]:  # np.interp는 단조 증가 필요 — 동률 구간 축약
            ps.append(p)
            vals.append(v)
    return int(round(float(np.interp(raw, vals, ps))))


def grade(score: int) -> str:
    if score >= 90:
        return "매우 강함"
    if score >= 75:
        return "강함"
    if score >= 60:
        return "우호적"
    if score >= 40:
        return "중립"
    if score >= 25:
        return "약세"
    return "위험"


def band_stats(score: int) -> dict:
    """해당 점수가 속한 10점 구간의 과거 10일 승률/평균 수익률."""
    for b in _cal()["bands"]:
        if b["lo"] <= score < b["hi"] or (b["hi"] == 100 and score >= b["lo"]):
            return b
    return _cal()["bands"][-1]

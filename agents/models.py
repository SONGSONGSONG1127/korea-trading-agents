# -*- coding: utf-8 -*-
"""공용 데이터 모델: 모든 판단 신호를 '증거 객체'로 저장한다."""
from dataclasses import dataclass


@dataclass
class Signal:
    name: str        # 예: "MA20 기울기"
    category: str    # 추세 | 모멘텀 | 거래량 | 위치/변동성
    score: float     # 카테고리 내 기여 점수 (예: +0.15)
    evidence: str    # 사람이 읽는 근거 문장 (예: "20일선이 5일 전 대비 +1.2% 상승")

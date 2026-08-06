# -*- coding: utf-8 -*-
"""
Agent 3: 전략 종합 에이전트 (v2)

- 종합 점수 = 기술 65% + 뉴스 35% (뉴스-기술 일치 시 ×1.1 / 상충 시 ×0.8)
- 강한 악재 공시 베토, 시장 역풍·고변동성 시 임계 상향
- 매수/손절/목표가는 스윙 로우·하이 + ATR로 산출, 손익비 1.5R 미달 시 진입 보류
- 판단 근거는 [결론 → 주도 근거 → 상충 신호 → 무효화 조건 → 뉴스 인용] 5단 구조

※ 교육/참고용 규칙 기반 로직이며 투자 권유가 아닙니다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from . import scoring
from .news_agent import NewsReport
from .technical_agent import CATEGORY_WEIGHTS, TechnicalReport

# 뉴스는 '확인용' — 점수에 반영하지 않는다 (과거 뉴스 데이터가 없어 백테스트로
# 검증 불가능하기 때문). 단, 유상증자·거래정지 등 객관적 악재 공시의 베토는 유지.
TECH_WEIGHT = 1.0
NEWS_WEIGHT = 0.0

# 임계 보정 근거: 유동성 상위 150종목 × 250거래일 풀드 백테스트(표본 4만 종목-일)
#  - 점수 ≥ 0.30: 10일 평균 +4.3%, 승률 54%, 신호 빈도 ~7% (기준선 +3.0%/53%)
#  - 약세 레짐(KOSPI<60일선) 기준선 -1.9%/승률 46% → 역풍 가산은 데이터로 정당화
#  - 점수 p99 = +0.38 → 가산 중첩 임계(0.45)는 도달 불가였으므로 가산 합산 +0.10로 캡
BASE_BUY_THR = 0.30
BASE_SELL_THR = -0.25
THR_BUMP_CAP = 0.10


@dataclass
class StrategyReport:
    opinion: str = "관망"
    reason_code: str = ""            # 관망 사유 (신호상충/스퀴즈/손익비부족/베토 등)
    total_score: float = 0.0
    score100: int = 0                # 0~100 — 과거 풀드 분포 백분위 (scoring.py)
    grade: str = ""                  # 매우 강함/강함/우호적/중립/약세/위험
    band: dict | None = None         # 이 점수 구간의 과거 10일 승률/평균 수익률
    confidence: str = ""             # "4축 중 3축 상방" 등
    buy_price: float = 0.0
    stop_loss: float = 0.0
    target_price: float = 0.0        # 1차 목표
    target2_price: float = 0.0       # 2차 목표
    rr: float = 0.0                  # 1차 목표 기준 손익비
    entry_basis: str = ""
    stop_basis: str = ""
    contrib_pct: dict[str, int] = field(default_factory=dict)
    conflicts: list[str] = field(default_factory=list)
    sections: dict[str, str] = field(default_factory=dict)  # 5단 근거
    summary: str = ""
    error: str | None = None


def _round_tick(price: float, up: bool = False) -> float:
    """KRX 호가 단위 반올림 (매수/손절은 내림, 목표는 올림)."""
    for limit, tick in [(2_000, 1), (5_000, 5), (20_000, 10),
                        (50_000, 50), (200_000, 100), (500_000, 500)]:
        if price < limit:
            break
    else:
        tick = 1_000
    f = math.ceil if up else math.floor
    return f(price / tick) * tick


def _swings(df) -> tuple[float, float]:
    """프랙탈(k=2) 스윙 로우/하이. center 윈도우라 최근 2일은 자동 미확정."""
    l, h = df["low"], df["high"]
    sl = l[l == l.rolling(5, center=True).min()]
    sh = h[h == h.rolling(5, center=True).max()]
    swing_low = float(sl.iloc[-1]) if len(sl) else float(l.tail(20).min())
    swing_high = float(sh.iloc[-1]) if len(sh) else float(h.tail(20).max())
    return swing_low, swing_high


def run(news: NewsReport, tech: TechnicalReport) -> StrategyReport:
    report = StrategyReport()
    if tech.error or tech.df is None:
        report.error = tech.error or "기술적 분석 데이터가 없습니다."
        return report

    df = tech.df
    price, a = tech.price, tech.atr

    # ── 종합 점수 (기술 100%, 뉴스는 확인용) ──────────────────────────
    total = TECH_WEIGHT * tech.score + NEWS_WEIGHT * news.score
    agree_note = ""
    if NEWS_WEIGHT > 0 and abs(tech.score) >= 0.2 and abs(news.score) >= 0.2:
        if np.sign(tech.score) == np.sign(news.score):
            total *= 1.1
            agree_note = "재료(뉴스)와 차트(기술)가 같은 방향 → 확신 가산(×1.1)"
        else:
            total *= 0.8
            agree_note = "재료-차트 불일치 → 확신 감산(×0.8)"
    total = float(np.clip(total, -1, 1))
    report.total_score = round(total, 3)
    report.score100 = scoring.to_score100(total)
    report.grade = scoring.grade(report.score100)
    report.band = scoring.band_stats(report.score100)

    # ── 의견 임계 (변동성·시장 레짐 — 이중 벌점 방지 위해 합산 +0.10로 캡) ──
    thr_notes = []
    bump = 0.0
    if tech.atr_pct > 0.05:
        bump += 0.10
        thr_notes.append(f"고변동 종목(하루 평균 진폭 {tech.atr_pct:.1%})")
    if tech.regime == "역풍":
        bump += 0.10
        thr_notes.append(f"시장 역풍({tech.regime_evidence})")
    bump = min(bump, THR_BUMP_CAP)
    buy_thr = BASE_BUY_THR + bump
    if thr_notes:
        thr_notes = [" · ".join(thr_notes) + f" → 매수 임계 +{bump:.2f} (상한 {THR_BUMP_CAP:.2f})"]

    up_cats = sum(1 for s in tech.cat_scores.values() if s >= 0.2)
    dn_cats = sum(1 for s in tech.cat_scores.values() if s <= -0.2)
    report.confidence = f"기술 4축 중 {up_cats}축 상방 / {dn_cats}축 하방"

    if total >= buy_thr and up_cats >= 1 and dn_cats == 0:
        report.opinion = "매수 고려"
    elif total <= BASE_SELL_THR:
        report.opinion = "비중 축소 고려"
    else:
        report.opinion = "관망"
        if total >= buy_thr:
            report.reason_code = "축 구성 미달 — 상방 축 없음 또는 하방 축 존재"
        elif total >= BASE_BUY_THR:
            report.reason_code = (
                f"보수화 임계 미달 — 종합 {total:+.2f}가 기본 임계(+{BASE_BUY_THR:.2f})는 넘었으나 "
                f"상향된 임계(+{buy_thr:.2f}) 미달"
            )
        elif any("상충" in f for f in tech.flags):
            report.reason_code = "신호 상충"
        elif any("압축" in f for f in tech.flags):
            report.reason_code = "변동성 압축 — 방향 결정 대기"
        else:
            report.reason_code = "종합 신호 약함"

    veto_note = ""
    if news.veto and report.opinion == "매수 고려":
        report.opinion = "관망"
        report.reason_code = "악재 공시 베토"
        worst = news.events[0]
        veto_note = f"최근 5일 내 강한 악재 「{worst.title}」({worst.sentiment:+.1f}) → 기술 신호와 무관하게 매수 보류"

    # ── 가격 산출: 스윙 + ATR ─────────────────────────────────────────
    swing_low, swing_high = _swings(df)

    if total >= 0.40 and tech.rvol >= 1.5:
        entry = price
        report.entry_basis = f"강신호 — 현재가 ~ +0.3ATR({price + 0.3 * a:,.0f}원) 이내 추격 허용"
    else:
        entry = min(price, max(tech.ma20, swing_low + 0.5 * a))
        report.entry_basis = f"눌림목 지정가 — max(20일선 {tech.ma20:,.0f}, 스윙로우+0.5ATR {swing_low + 0.5 * a:,.0f}) 부근"

    stop_struct = swing_low - 0.5 * a
    if 0 < entry - stop_struct <= 2.5 * a:
        stop = stop_struct
        report.stop_basis = f"스윙로우 {swing_low:,.0f}원 − 0.5ATR (구조적 손절)"
    else:
        stop = entry - 2.0 * a
        report.stop_basis = "지지선 원거리 → 2ATR 변동성 손절"

    risk = entry - stop
    resistance = max(swing_high, float(df["high"].tail(20).max()))
    rr_to_res = (resistance - entry) / risk if risk > 0 else 0.0
    if report.opinion == "매수 고려" and rr_to_res < 1.5:
        report.opinion = "관망"
        report.reason_code = f"손익비 부족 — 저항선까지 {rr_to_res:.1f}R뿐"

    target1 = min(resistance, entry + 2 * risk)
    target2 = entry + 3 * risk
    report.buy_price = _round_tick(entry)
    report.stop_loss = _round_tick(stop)
    report.target_price = _round_tick(target1, up=True)
    report.target2_price = _round_tick(target2, up=True)
    report.rr = round((report.target_price - report.buy_price) / max(report.buy_price - report.stop_loss, 1), 1)

    # ── 기여도 / 상충 ─────────────────────────────────────────────────
    contrib = {cat: CATEGORY_WEIGHTS[cat] * s * TECH_WEIGHT for cat, s in tech.cat_scores.items()}
    if NEWS_WEIGHT > 0:
        contrib["뉴스"] = NEWS_WEIGHT * news.score
    total_abs = sum(abs(v) for v in contrib.values()) + 1e-9
    report.contrib_pct = {k: round(abs(v) / total_abs * 100) for k, v in contrib.items()}

    direction = 1 if total >= 0 else -1
    report.conflicts = [
        f"{s.name}: {s.evidence} ({s.score:+.2f})"
        for s in tech.signals
        if np.sign(s.score) == -direction and abs(s.score) >= 0.1
    ]
    if np.sign(news.score) == -direction and abs(news.score) >= 0.2:
        report.conflicts.append(f"(참고) 뉴스 감성 {news.score:+.2f}이 기술 방향과 반대 — 점수 미반영")

    # ── 5단 근거 텍스트 ───────────────────────────────────────────────
    top_cats = sorted(
        [(k, v) for k, v in contrib.items()],
        key=lambda x: abs(x[1]), reverse=True,
    )[:2]
    lead_lines = []
    for cat, _v in top_cats:
        if cat == "뉴스":
            ev = news.summary
        else:
            best = sorted(
                [s for s in tech.signals if s.category == cat],
                key=lambda s: abs(s.score), reverse=True,
            )[:2]
            ev = " / ".join(s.evidence for s in best)
        lead_lines.append(f"**{cat}** (기여 {report.contrib_pct.get(cat, 0)}%): {ev}")

    conclusion = (
        f"**{report.opinion}** · 종합 {total:+.2f} (기술 100%, 뉴스는 확인용) · {report.confidence}"
        + (f" · 합의 배수 ×{tech.confluence_mult:.2f}" if tech.confluence_mult != 1.0 else "")
        + f" · 매수 임계 +{buy_thr:.2f} (풀드 백테스트 보정값)"
    )
    if report.reason_code:
        conclusion += f" · 사유: {report.reason_code}"

    invalidation = (
        f"종가 기준 {report.stop_loss:,.0f}원({report.stop_basis}) 이탈 시 시나리오 폐기. "
        f"1차 목표 {report.target_price:,.0f}원(손익비 1:{report.rr}), 2차 목표 {report.target2_price:,.0f}원(3R)."
    )
    if veto_note:
        invalidation = veto_note + "\n\n" + invalidation

    news_lines = [
        f"「{it.title}」 ({it.date}, {it.sentiment:+.1f}"
        + (f", 유사 기사 {it.dup_count}건 병합" if it.dup_count > 1 else "") + ")"
        for it in (news.events or sorted(news.items, key=lambda x: abs(x.sentiment), reverse=True)[:3])[:3]
    ]

    extra = [n for n in [agree_note, *thr_notes] if n]
    report.sections = {
        "결론": conclusion + ("\n\n" + " · ".join(extra) if extra else ""),
        "주도 근거": "\n\n".join(lead_lines),
        "상충 신호": "\n\n".join("• " + c for c in report.conflicts) if report.conflicts else "결론과 반대 방향의 유의미한 신호 없음",
        "무효화 조건": invalidation,
        "뉴스 인용": "\n\n".join("• " + n for n in news_lines) if news_lines else "유의미한 기사 없음",
    }

    report.summary = (
        f"종합 {total:+.2f} → '{report.opinion}'"
        + (f" ({report.reason_code})" if report.reason_code else "")
        + f". 매수 {report.buy_price:,.0f} / 손절 {report.stop_loss:,.0f}"
        f"({(report.stop_loss / report.buy_price - 1) * 100:.1f}%) / "
        f"목표 {report.target_price:,.0f}(+{(report.target_price / report.buy_price - 1) * 100:.1f}%), "
        f"손익비 1:{report.rr}."
    )
    return report

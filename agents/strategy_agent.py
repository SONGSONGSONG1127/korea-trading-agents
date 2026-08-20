# -*- coding: utf-8 -*-
"""
Agent 3: 전략 종합 에이전트 (v3)

- 종합 점수 = 기술 60% + 펀더멘탈 40% (combined_score)
- 의견: combined_score ≥ 65 and up_cats ≥ 1 → 매수 고려 (dn_cats 0 요구 제거)
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
from .fundamental_agent import FundamentalReport
from .news_agent import NewsReport
from .technical_agent import CATEGORY_WEIGHTS, TechnicalReport

TECH_WEIGHT = 1.0
NEWS_WEIGHT = 0.0

# 기술 점수 백분위(score100)와 펀더멘탈 점수(fund_score) 합산 비율
TECH_COMBINED_W = 0.60
FUND_COMBINED_W = 0.40

BASE_BUY_THR = 0.30
BASE_SELL_THR = -0.25
THR_BUMP_CAP = 0.10

# combined_score 기준 매수 임계 (0~100 스케일)
COMBINED_BUY_THR = 55      # 기술 + 펀더멘탈 합산 55점 이상
COMBINED_STRONG_THR = 65   # 65점 이상: 강한 매수 고려


@dataclass
class StrategyReport:
    opinion: str = "관망"
    reason_code: str = ""
    total_score: float = 0.0
    score100: int = 0                # 기술 백분위 (0~100)
    grade: str = ""
    band: dict | None = None
    fund_score: int = 50             # 펀더멘탈 점수 (0~100)
    fund_grade: str = ""
    combined_score: int = 50         # 기술+펀더멘탈 합산 (0~100)
    confidence: str = ""
    buy_price: float = 0.0
    stop_loss: float = 0.0
    target_price: float = 0.0
    target2_price: float = 0.0
    rr: float = 0.0
    entry_basis: str = ""
    stop_basis: str = ""
    contrib_pct: dict[str, int] = field(default_factory=dict)
    conflicts: list[str] = field(default_factory=list)
    sections: dict[str, str] = field(default_factory=dict)
    summary: str = ""
    error: str | None = None


def _round_tick(price: float, up: bool = False) -> float:
    for limit, tick in [(2_000, 1), (5_000, 5), (20_000, 10),
                        (50_000, 50), (200_000, 100), (500_000, 500)]:
        if price < limit:
            break
    else:
        tick = 1_000
    f = math.ceil if up else math.floor
    return f(price / tick) * tick


def _swings(df) -> tuple[float, float]:
    l, h = df["low"], df["high"]
    sl = l[l == l.rolling(5, center=True).min()]
    sh = h[h == h.rolling(5, center=True).max()]
    swing_low = float(sl.iloc[-1]) if len(sl) else float(l.tail(20).min())
    swing_high = float(sh.iloc[-1]) if len(sh) else float(h.tail(20).max())
    return swing_low, swing_high


def run(news: NewsReport, tech: TechnicalReport,
        fund: FundamentalReport | None = None) -> StrategyReport:
    report = StrategyReport()
    if tech.error or tech.df is None:
        report.error = tech.error or "기술적 분석 데이터가 없습니다."
        return report

    df = tech.df
    price, a = tech.price, tech.atr

    # ── 기술 점수 ───────────────────────────────────────────────────────
    total = float(np.clip(TECH_WEIGHT * tech.score, -1, 1))
    report.total_score = round(total, 3)
    report.score100 = scoring.to_score100(total)
    report.grade = scoring.grade(report.score100)
    report.band = scoring.band_stats(report.score100)

    # ── 펀더멘탈 점수 ───────────────────────────────────────────────────
    if fund and not fund.error:
        report.fund_score = fund.fund_score
        report.fund_grade = fund.fund_grade
    else:
        report.fund_score = 50   # 데이터 없으면 중립
        report.fund_grade = "미확인"

    # ── 합산 점수 (기술 60% + 펀더멘탈 40%) ────────────────────────────
    report.combined_score = round(
        TECH_COMBINED_W * report.score100 + FUND_COMBINED_W * report.fund_score
    )

    # ── 의견 임계 (변동성·시장 레짐 보수화) ────────────────────────────
    thr_notes = []
    bump_combined = 0
    bump_raw = 0.0
    if tech.atr_pct > 0.05:
        bump_raw += 0.10
        bump_combined += 5
        thr_notes.append(f"고변동 종목(하루 평균 진폭 {tech.atr_pct:.1%})")
    if tech.regime == "역풍":
        bump_raw += 0.10
        bump_combined += 5
        thr_notes.append(f"시장 역풍({tech.regime_evidence})")
    bump_raw = min(bump_raw, THR_BUMP_CAP)
    bump_combined = min(bump_combined, 10)

    buy_thr = BASE_BUY_THR + bump_raw
    combined_buy_thr = COMBINED_BUY_THR + bump_combined

    if thr_notes:
        thr_notes = [" · ".join(thr_notes) + f" → 합산 임계 +{bump_combined}점"]

    up_cats = sum(1 for s in tech.cat_scores.values() if s >= 0.2)
    dn_cats = sum(1 for s in tech.cat_scores.values() if s <= -0.2)
    report.confidence = f"기술 4축 중 {up_cats}축 상방 / {dn_cats}축 하방"

    # ── 의견 결정 (combined_score 기반, dn_cats 0 요건 제거) ─────────────
    # 펀더멘탈 우량(≥65) + 기술 강함(score100≥70): 강한 매수
    # 합산 ≥ combined_buy_thr + up_cats ≥ 1: 매수 고려
    if report.combined_score >= COMBINED_STRONG_THR and up_cats >= 1:
        report.opinion = "매수 고려"
    elif report.combined_score >= combined_buy_thr and up_cats >= 1 and dn_cats <= 1:
        report.opinion = "매수 고려"
    elif total <= BASE_SELL_THR and report.fund_score < 40:
        report.opinion = "비중 축소 고려"
    elif total <= BASE_SELL_THR:
        report.opinion = "관망"
        report.reason_code = "기술 약세 (펀더멘탈은 보통 이상)"
    else:
        report.opinion = "관망"
        if report.combined_score >= combined_buy_thr:
            report.reason_code = "축 구성 미달 — 상방 축 없음 또는 하방 축 과다"
        elif total >= BASE_BUY_THR:
            report.reason_code = f"합산 점수 미달 ({report.combined_score}점, 임계 {combined_buy_thr}점)"
        elif any("상충" in f for f in tech.flags):
            report.reason_code = "신호 상충"
        elif any("압축" in f for f in tech.flags):
            report.reason_code = "변동성 압축 — 방향 결정 대기"
        else:
            report.reason_code = "종합 신호 약함"

    # 펀더멘탈 위험 경고 (펀더멘탈 점수 32 미만 = "위험" 등급)
    fund_caution = ""
    if fund and not fund.error and fund.fund_score < 32:
        fund_caution = f"⚠️ 펀더멘탈 {fund.fund_score}점/{fund.fund_grade} — 재무 악화 경고"
        if report.opinion == "매수 고려":
            report.opinion = "관망"
            report.reason_code = f"펀더멘탈 위험 ({fund.fund_score}점)"

    # 뉴스 베토
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
        report.entry_basis = (
            f"눌림목 지정가 — max(20일선 {tech.ma20:,.0f}, "
            f"스윙로우+0.5ATR {swing_low + 0.5 * a:,.0f}) 부근"
        )

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
    report.rr = round(
        (report.target_price - report.buy_price) / max(report.buy_price - report.stop_loss, 1), 1
    )

    # ── 기여도 / 상충 ─────────────────────────────────────────────────
    contrib = {cat: CATEGORY_WEIGHTS[cat] * s for cat, s in tech.cat_scores.items()}
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
    top_cats = sorted(contrib.items(), key=lambda x: abs(x[1]), reverse=True)[:2]
    lead_lines = []
    for cat, _v in top_cats:
        best = sorted(
            [s for s in tech.signals if s.category == cat],
            key=lambda s: abs(s.score), reverse=True,
        )[:2]
        ev = " / ".join(s.evidence for s in best)
        lead_lines.append(f"**{cat}** (기여 {report.contrib_pct.get(cat, 0)}%): {ev}")

    # 펀더멘탈 근거 추가
    fund_lines = ""
    if fund and not fund.error:
        items = []
        if fund.per:
            rel = ""
            if fund.sector_per and fund.sector_per > 0:
                rel = f" (업종 {fund.sector_per:.1f}배 대비 {'할인' if fund.per < fund.sector_per else '할증'})"
            items.append(f"PER {fund.per:.1f}배{rel}")
        if fund.pbr:
            items.append(f"PBR {fund.pbr:.2f}배")
        if fund.roe:
            items.append(f"ROE {fund.roe:.1f}%")
        if fund.sector_per is not None and fund.per is not None:
            rel = (fund.sector_per - fund.per) / fund.sector_per * 100
            items.append(f"업종PER대비 {rel:+.0f}%")
        if fund.div_yield:
            items.append(f"배당 {fund.div_yield:.2f}%")
        fund_lines = (
            f"\n\n**펀더멘탈** ({fund.fund_grade} {fund.fund_score}점/100 · {fund.data_quality}): "
            + " · ".join(items)
        )
        if fund_caution:
            fund_lines += f"\n\n{fund_caution}"

    conclusion = (
        f"**{report.opinion}** · 기술 {report.score100}점 / 펀더멘탈 {report.fund_score}점 → "
        f"합산 **{report.combined_score}점** (기술60%+펀더40%) · {report.confidence}"
        + (f" · 합의 배수 ×{tech.confluence_mult:.2f}" if tech.confluence_mult != 1.0 else "")
    )
    if report.reason_code:
        conclusion += f"\n\n사유: {report.reason_code}"

    thr_extra = " · ".join(thr_notes) if thr_notes else ""

    invalidation = (
        f"종가 기준 {report.stop_loss:,.0f}원({report.stop_basis}) 이탈 시 시나리오 폐기. "
        f"1차 목표 {report.target_price:,.0f}원(손익비 1:{report.rr}), "
        f"2차 목표 {report.target2_price:,.0f}원(3R)."
    )
    if veto_note:
        invalidation = veto_note + "\n\n" + invalidation

    news_lines = [
        f"「{it.title}」 ({it.date}, {it.sentiment:+.1f}"
        + (f", 유사 기사 {it.dup_count}건 병합" if it.dup_count > 1 else "") + ")"
        for it in (news.events or sorted(news.items, key=lambda x: abs(x.sentiment), reverse=True)[:3])[:3]
    ]

    report.sections = {
        "결론": conclusion + ("\n\n" + thr_extra if thr_extra else ""),
        "주도 근거": "\n\n".join(lead_lines) + fund_lines,
        "상충 신호": (
            "\n\n".join("• " + c for c in report.conflicts)
            if report.conflicts
            else "결론과 반대 방향의 유의미한 신호 없음"
        ),
        "무효화 조건": invalidation,
        "뉴스 인용": (
            "\n\n".join("• " + n for n in news_lines)
            if news_lines
            else "유의미한 기사 없음"
        ),
    }

    report.summary = (
        f"합산 {report.combined_score}점 (기술 {report.score100} / 펀더 {report.fund_score}) → "
        f"'{report.opinion}'"
        + (f" ({report.reason_code})" if report.reason_code else "")
        + f". 매수 {report.buy_price:,.0f} / 손절 {report.stop_loss:,.0f}"
        f"({(report.stop_loss / report.buy_price - 1) * 100:.1f}%) / "
        f"목표 {report.target_price:,.0f}(+{(report.target_price / report.buy_price - 1) * 100:.1f}%), "
        f"손익비 1:{report.rr}."
    )
    return report

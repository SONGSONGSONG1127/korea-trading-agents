# -*- coding: utf-8 -*-
"""
Agent 3: 펀더멘탈/재무 에이전트 (v3)

데이터 소스 (우선순위):
  1. DART OpenAPI (dart_agent) — 실제 재무제표: 매출/영업이익/부채비율/ROA/Piotroski F-Score
  2. 네이버 증권 정적 HTML — PER/PBR/업종PER/ROE분기/배당

스코어링 학술 근거:
  - Piotroski (2000, JAR): 9개 이진 재무지표 → F-Score, KOSPI 적용 연구 다수
  - Fama-French (1992): 저PBR + 고ROE → 초과수익 (Profitability factor)
  - Damodaran (2012): 업종 상대 PER 할인이 밸류에이션 핵심
  - 한국 시장 보정: KOSPI 평균 PBR 1.1~1.5, 대형 성장주 2~4 정상 범위

점수 구조 (0~100):
  밸류에이션 (0~50) : PER + 업종PER 상대 + PBR Gordon 모형
  품질      (0~30) : ROE 수준·안정성 + Piotroski F-Score (DART 있을 때 블렌딩)
  성장·수익  (0~20) : 배당·이익수익률 + 매출·영업이익 성장률 (DART 있을 때)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from statistics import mean, stdev
from typing import TYPE_CHECKING

import requests
from bs4 import BeautifulSoup

if TYPE_CHECKING:
    from . import dart_agent as _dart_mod

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Referer": "https://finance.naver.com/",
}


@dataclass
class FundamentalReport:
    stock_name: str
    code: str

    # ── 네이버 정적 지표 ──────────────────────────────────────────────────
    per: float | None = None
    sector_per: float | None = None
    pbr: float | None = None
    eps: float | None = None
    div_yield: float | None = None
    roe_quarters: list[float] = field(default_factory=list)
    roe: float | None = None
    roe_trend: str = ""

    # ── DART 실제 재무 지표 (억원) ─────────────────────────────────────────
    revenue:          float | None = None   # 매출액
    op_profit:        float | None = None   # 영업이익
    net_income:       float | None = None   # 당기순이익
    prev_revenue:     float | None = None
    prev_op_profit:   float | None = None
    roa:              float | None = None   # %
    debt_ratio:       float | None = None   # %
    op_margin:        float | None = None   # %
    revenue_growth:   float | None = None   # % YoY
    op_profit_growth: float | None = None   # % YoY
    interest_coverage: float | None = None  # 배
    f_score:          int | None = None     # Piotroski (0~9), None = DART 없음
    f_details:        list[str] = field(default_factory=list)
    dart_available:   bool = False

    # ── 세부 점수 ─────────────────────────────────────────────────────────
    valuation_score: int = 0   # 0~50
    quality_score:   int = 0   # 0~30
    income_score:    int = 0   # 0~20

    # ── 종합 ──────────────────────────────────────────────────────────────
    fund_score: int = 50
    fund_grade: str = "보통"
    data_quality: str = ""     # "완전(DART)" / "완전" / "부분" / "기본"

    summary: str = ""
    error: str | None = None


# ── 유틸 ─────────────────────────────────────────────────────────────────

def _get(url: str) -> BeautifulSoup:
    res = requests.get(url, headers=HEADERS, timeout=10)
    res.raise_for_status()
    if "charset" not in (res.headers.get("Content-Type") or "").lower():
        res.encoding = "euc-kr"
    return BeautifulSoup(res.text, "lxml")


def _num(text: str) -> float | None:
    t = re.sub(r"[,\s]", "", text.strip())
    t = re.sub(r"[%배억원]", "", t)
    if not t or t in ("N/A", "-", "적자", "흑자", "n/a", "해당없음"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


# ── 스크래핑 ──────────────────────────────────────────────────────────────

def _fetch_main(code: str) -> dict:
    """네이버 종목 메인에서 PER·PBR·업종PER·ROE(분기)·배당 수집."""
    soup = _get(f"https://finance.naver.com/item/main.naver?code={code}")
    result: dict = {}

    # PER / PBR — ID 셀렉터 (가장 안정적)
    for key, eid in [("per", "_per"), ("pbr", "_pbr"), ("eps", "_eps")]:
        tag = soup.select_one(f"#{eid}")
        if tag:
            v = _num(tag.get_text(strip=True))
            if v is not None:
                result[key] = v

    # 업종PER / 배당 — per_table 행 순서 기반 (2번째 행=업종PER, 4번째=배당)
    # 인코딩 문제로 th 텍스트 비교 불가 → 위치로 파싱
    per_tbl = soup.select_one("table.per_table")
    if per_tbl:
        rows = per_tbl.select("tr")
        def _first_num(row_idx: int) -> float | None:
            if row_idx >= len(rows):
                return None
            tds = rows[row_idx].select("td")
            if not tds:
                return None
            # td 텍스트에서 첫 번째 숫자 추출 ("6.00배|47,816원" → 6.0)
            nums = re.findall(r"\d+\.?\d*", tds[0].get_text(strip=True))
            return float(nums[0]) if nums else None
        result["sector_per"] = _first_num(1)   # 2번째 행: 업종PER
        result["div_yield"]  = _first_num(3)   # 4번째 행: 배당수익률

    # ROE 분기 데이터 — "ROE"가 포함된 첫 번째 th 행 (분기 누적 ROE)
    # 인코딩 문제로 "분기" 텍스트 매칭 불가 → "ROE" 포함 첫 행 사용
    roe_q: list[float] = []
    for tbl in soup.find_all("table"):
        if roe_q:
            break
        for row in tbl.find_all("tr"):
            ths = row.find_all("th")
            tds = row.find_all("td")
            for th in ths:
                if "ROE" in th.get_text(strip=True) and len(tds) >= 4:
                    vals = [_num(td.get_text(strip=True)) for td in tds]
                    raw = [v for v in vals if v is not None]
                    # 이상치 제거: 단기 분기 ROE가 50% 초과면 특수 항목 → 제외
                    roe_q = [v for v in raw if v <= 50]
                    break
            if roe_q:
                break

    result["roe_quarters"] = roe_q
    return result


# ── 스코어링 ──────────────────────────────────────────────────────────────

def _valuation_score(per: float | None, pbr: float | None,
                     sector_per: float | None, roe: float | None) -> int:
    """
    밸류에이션 점수 (0~50).

    한국 시장 보정 기준:
    - KOSPI 평균 PBR ~1.1~1.5, 대형 성장주 PBR 2~4 정상 범위
    - 업종 PER 대비 할인 여부가 절대 PER 수준보다 중요 (Damodaran 2012)
    - ROE 대비 PBR: 적정 PBR = ROE / 요구수익률(10%) (고든 성장모형)
    """
    score = 0

    # ① PER 점수 (0~20) — 절대 + 업종 상대
    if per is not None and per > 0:
        # 절대 기준
        if per < 8:
            base = 18
        elif per < 12:
            base = 15
        elif per < 17:
            base = 12
        elif per < 22:
            base = 9
        elif per < 30:
            base = 5
        else:
            base = 2
        score += base
        # 업종 PER 대비 할인 보너스 (최대 +3)
        if sector_per and sector_per > 0:
            rel = (sector_per - per) / sector_per
            score += min(3, max(0, round(rel * 6)))
    elif per is not None and per <= 0:
        score += 0   # 적자 → 0

    # ② PBR 점수 (0~20) — ROE 대비 상대 밸류 중심
    if pbr is not None and pbr > 0:
        # 고든 모형: 적정 PBR = ROE / 요구수익률(8% — 한국 대형주 기준)
        # 요구수익률 10%는 너무 엄격 → 8% 사용 (무위험 3.5% + 리스크 4.5%)
        if roe and roe > 0:
            fair = roe / 8.0
            ratio = pbr / fair   # 1.0 = 적정가
            if ratio < 0.5:
                score += 20
            elif ratio < 0.8:
                score += 17
            elif ratio < 1.0:
                score += 14
            elif ratio < 1.4:
                score += 11
            elif ratio < 2.0:
                score += 8
            elif ratio < 3.0:
                score += 5
            elif ratio < 4.5:
                score += 3
            else:
                score += 1
        else:
            # ROE 없을 때 절대 기준 (한국 시장 중심)
            if pbr < 0.7:
                score += 20
            elif pbr < 1.0:
                score += 16
            elif pbr < 1.5:
                score += 12
            elif pbr < 2.5:
                score += 8
            elif pbr < 4.0:
                score += 4
            else:
                score += 1

    # ③ PER 결측 시 부분 보정 (최대 10점 공백 채우기)
    # → 데이터 없으면 5점(중립) 부여
    if per is None and pbr is None:
        score = 25   # 완전 중립

    return min(50, max(0, score))


def _quality_score(
    roe: float | None, roe_quarters: list[float],
    f_score: int | None = None,
) -> tuple[int, str]:
    """
    품질 점수 (0~30).
    DART 있을 때: ROE(0~20) + Piotroski F-Score(0~10) 블렌딩
    DART 없을 때: ROE(0~18) + 안정성(0~12)
    """
    if f_score is not None:
        # ── DART 있는 경우: ROE(0~20) + F-Score(0~10) ──
        roe_pts = 0
        if roe is not None:
            if roe >= 25:   roe_pts = 20
            elif roe >= 20: roe_pts = 18
            elif roe >= 15: roe_pts = 15
            elif roe >= 10: roe_pts = 11
            elif roe >= 5:  roe_pts = 7
            elif roe > 0:   roe_pts = 3
        f_pts = round(f_score / 9 * 10)  # 0~10
        trend = "개선" if f_score >= 7 else ("악화" if f_score <= 3 else "안정")
        return min(30, roe_pts + f_pts), trend

    # ── DART 없는 경우: 기존 ROE 기반 ──
    if roe is None or not roe_quarters:
        return 15, "미확인"

    score = 0

    # ROE 수준 (0~18)
    if roe >= 25:   score += 18
    elif roe >= 20: score += 16
    elif roe >= 15: score += 13
    elif roe >= 10: score += 10
    elif roe >= 5:  score += 6
    elif roe > 0:   score += 2

    # ROE 안정성 / 트렌드 (0~12)
    trend = "안정"
    if len(roe_quarters) >= 4:
        recent4 = roe_quarters[:4]
        older4  = roe_quarters[4:8] if len(roe_quarters) >= 8 else None
        try:
            cv = stdev(recent4) / abs(mean(recent4)) if mean(recent4) != 0 else 99
            if cv < 0.20:   score += 8
            elif cv < 0.40: score += 5
            elif cv < 0.65: score += 2
        except Exception:
            pass
        if older4:
            r_now, r_old = mean(recent4), mean(older4)
            if r_now > r_old * 1.1:   score += 4; trend = "개선"
            elif r_now < r_old * 0.9: score += 1; trend = "악화"
            else:                      score += 2
        else:
            score += 2

    return min(30, max(0, score)), trend


def _income_score(
    per: float | None, div_yield: float | None,
    revenue_growth: float | None = None,
    op_profit_growth: float | None = None,
) -> int:
    """
    이익수익률·성장·배당 점수 (0~20).
    DART 있을 때 성장률 점수(0~8) 추가, 배당·이익수익률 각각 조정.
    """
    score = 0
    has_dart_growth = revenue_growth is not None or op_profit_growth is not None

    if has_dart_growth:
        # 이익수익률 (0~7)
        if per and per > 0:
            ey = 1 / per * 100
            if ey >= 12:   score += 7
            elif ey >= 8:  score += 6
            elif ey >= 6:  score += 4
            elif ey >= 4:  score += 2
            else:          score += 1

        # 성장률 (0~9): 매출+영업이익 성장률 평균
        growths = [g for g in [revenue_growth, op_profit_growth] if g is not None]
        if growths:
            g_avg = mean(growths)
            if g_avg >= 30:    score += 9
            elif g_avg >= 20:  score += 7
            elif g_avg >= 10:  score += 5
            elif g_avg >= 3:   score += 3
            elif g_avg >= 0:   score += 1
            else:              score += 0   # 역성장

        # 배당 (0~4)
        if div_yield:
            if div_yield >= 4:   score += 4
            elif div_yield >= 3: score += 3
            elif div_yield >= 2: score += 2
            elif div_yield >= 1: score += 1

    else:
        # 이익수익률 (0~12)
        if per and per > 0:
            ey = 1 / per * 100
            if ey >= 12:   score += 12
            elif ey >= 8:  score += 10
            elif ey >= 6:  score += 7
            elif ey >= 4:  score += 4
            else:          score += 1

        # 배당 (0~8)
        if div_yield:
            if div_yield >= 4:   score += 8
            elif div_yield >= 3: score += 6
            elif div_yield >= 2: score += 4
            elif div_yield >= 1: score += 2

    return min(20, max(0, score))


def _grade(score: int) -> str:
    if score >= 80:
        return "매우 우량"
    if score >= 65:
        return "우량"
    if score >= 48:
        return "보통"
    if score >= 32:
        return "취약"
    return "위험"


# ── 메인 ─────────────────────────────────────────────────────────────────

def run(code: str, stock_name: str = "") -> FundamentalReport:
    code = re.sub(r"\D", "", code).zfill(6)
    report = FundamentalReport(stock_name=stock_name or code, code=code)

    # ── 1. 네이버 정적 데이터 ───────────────────────────────────────────────
    try:
        data = _fetch_main(code)
    except Exception as e:
        report.error = f"수집 실패: {e}"
        return report

    report.per        = data.get("per")
    report.sector_per = data.get("sector_per")
    report.pbr        = data.get("pbr")
    report.eps        = data.get("eps")
    report.div_yield  = data.get("div_yield")
    report.roe_quarters = data.get("roe_quarters", [])

    # ROE: 전체 분기 중앙값 (사이클 저점 과도 반영 방지)
    from statistics import median as _median
    all_q = [v for v in report.roe_quarters if v is not None]
    report.roe = round(_median(all_q), 2) if all_q else None

    # ── 2. DART 재무제표 (API 키 있을 때만) ────────────────────────────────
    dart_fin = None
    try:
        from . import dart_agent
        dart_fin = dart_agent.get_financials(code)
    except Exception:
        pass

    if dart_fin is not None:
        report.dart_available    = True
        report.revenue           = dart_fin.revenue
        report.op_profit         = dart_fin.op_profit
        report.net_income        = dart_fin.net_income
        report.prev_revenue      = dart_fin.prev_revenue
        report.prev_op_profit    = dart_fin.prev_op_profit
        report.roa               = dart_fin.roa
        report.debt_ratio        = dart_fin.debt_ratio
        report.op_margin         = dart_fin.op_margin
        report.revenue_growth    = dart_fin.revenue_growth
        report.op_profit_growth  = dart_fin.op_profit_growth
        report.interest_coverage = dart_fin.interest_coverage
        report.f_score           = dart_fin.f_score
        report.f_details         = dart_fin.f_details
        # DART ROE가 있으면 Naver ROE 보정 (연간 순이익/자본 직접 계산)
        if dart_fin.net_income and dart_fin.prev_equity and dart_fin.prev_equity > 0:
            dart_roe = dart_fin.net_income / dart_fin.prev_equity * 100
            if report.roe is None:
                report.roe = round(dart_roe, 2)

    # ── 데이터 품질 ────────────────────────────────────────────────────────
    if report.dart_available and report.per and report.pbr:
        report.data_quality = "완전(DART)"
    elif report.per and report.pbr and report.roe:
        report.data_quality = "완전"
    elif report.per or report.pbr:
        report.data_quality = "부분"
    else:
        report.data_quality = "기본"

    # ── 스코어링 ───────────────────────────────────────────────────────────
    v = _valuation_score(report.per, report.pbr, report.sector_per, report.roe)
    q, report.roe_trend = _quality_score(
        report.roe, report.roe_quarters, report.f_score
    )
    i = _income_score(
        report.per, report.div_yield,
        report.revenue_growth, report.op_profit_growth,
    )

    report.valuation_score = v
    report.quality_score   = q
    report.income_score    = i
    report.fund_score      = v + q + i
    report.fund_grade      = _grade(report.fund_score)

    # ── 요약 문자열 ────────────────────────────────────────────────────────
    parts = []
    if report.per:
        s = f"PER {report.per:.1f}배"
        if report.sector_per:
            rel = (report.sector_per - report.per) / report.sector_per * 100
            s += f" (업종 {report.sector_per:.1f}배 대비 {rel:+.0f}%)"
        parts.append(s)
    if report.pbr:
        parts.append(f"PBR {report.pbr:.2f}배")
    if report.roe:
        parts.append(f"ROE {report.roe:.1f}% ({report.roe_trend})")
    if report.roa:
        parts.append(f"ROA {report.roa:.1f}%")
    if report.debt_ratio:
        parts.append(f"부채비율 {report.debt_ratio:.0f}%")
    if report.revenue_growth is not None:
        parts.append(f"매출성장 {report.revenue_growth:+.1f}%")
    if report.f_score is not None:
        parts.append(f"F-Score {report.f_score}/9")
    if report.div_yield:
        parts.append(f"배당 {report.div_yield:.2f}%")

    dart_tag = " [DART]" if report.dart_available else ""
    report.summary = (
        f"펀더멘탈 {report.fund_score}점/{report.fund_grade}"
        f" (밸류 {v}/50 · 품질 {q}/30 · 성장·수익 {i}/20)"
        f" [{report.data_quality}]{dart_tag}. "
        + ", ".join(parts) + "."
    )
    return report

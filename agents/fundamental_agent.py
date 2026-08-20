# -*- coding: utf-8 -*-
"""
Agent 3: 펀더멘탈/재무 에이전트 (v2)

네이버 증권 메인 페이지에서 정적 HTML로 수집 가능한 지표만 사용:
  - PER, PBR  : #_per / #_pbr 셀렉터
  - 업종 PER  : per_table 파싱
  - ROE 분기  : 메인 페이지 테이블 'ROE(분기누적)' 행 → 연환산 ROE 추정
  - 배당수익률 : per_table

스코어링 학술 근거:
  - Fama-French (1992): 저PBR + 고ROE 종목의 초과수익 (한국 KOSPI 적용 다수 연구 확인)
  - Damodaran (2012): PER < PEG, 업종 상대 PER 할인이 핵심 밸류에이션 지표
  - 한국 시장 보정: KOSPI 평균 PBR ~1.1~1.5, 반도체·플랫폼 대형주는 PBR 2~4도 정상
    → Graham 절대 기준(PBR<1.5) 대신 업종 상대·ROE 대비 밸류 사용

데이터 제약:
  영업이익·매출·부채비율 등 상세 재무 데이터는 네이버가 JavaScript/iframe으로 로딩해
  정적 스크래핑 불가 → 수집된 지표로만 점수 계산, 나머지는 중립값 처리
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from statistics import mean, stdev

import requests
from bs4 import BeautifulSoup

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

    # 수집된 지표
    per: float | None = None
    sector_per: float | None = None
    pbr: float | None = None
    eps: float | None = None
    div_yield: float | None = None       # 배당수익률 (%)
    roe_quarters: list[float] = field(default_factory=list)   # 분기 ROE (최신→과거)
    roe: float | None = None             # 최근 4분기 평균 ROE
    roe_trend: str = ""                  # "개선" / "악화" / "안정"

    # 세부 점수 (0~100)
    valuation_score: int = 0    # PER·PBR·업종 상대 밸류 (0~50)
    quality_score: int = 0      # ROE 수준·안정성 (0~30)
    income_score: int = 0       # 배당·이익 수익률 (0~20)

    # 종합 펀더멘탈 점수 (0~100)
    fund_score: int = 50        # 데이터 부족 시 중립 50
    fund_grade: str = "보통"
    data_quality: str = ""      # "완전" / "부분" / "기본"

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


def _quality_score(roe: float | None, roe_quarters: list[float]) -> tuple[int, str]:
    """
    ROE 기반 품질 점수 (0~30).
    Fama-French: 고ROE는 수익성 팩터(RMW)의 핵심 지표.
    """
    if roe is None or not roe_quarters:
        return 15, "미확인"   # 데이터 없으면 중립 15

    score = 0

    # ROE 수준 (0~18)
    if roe >= 25:
        score += 18
    elif roe >= 20:
        score += 16
    elif roe >= 15:
        score += 13
    elif roe >= 10:
        score += 10
    elif roe >= 5:
        score += 6
    elif roe > 0:
        score += 2
    else:
        score += 0  # 적자

    # ROE 안정성 / 트렌드 (0~12)
    trend = "안정"
    if len(roe_quarters) >= 4:
        recent4 = roe_quarters[:4]
        older4 = roe_quarters[4:8] if len(roe_quarters) >= 8 else None
        try:
            std = stdev(recent4)
            avg = mean(recent4)
            cv = std / abs(avg) if avg != 0 else 99
            # 변동성 낮을수록 안정 (임계값 완화: 경기순환주 감안)
            if cv < 0.20:
                score += 8
            elif cv < 0.40:
                score += 5
            elif cv < 0.65:
                score += 2
        except Exception:
            pass
        if older4:
            r_now = mean(recent4)
            r_old = mean(older4)
            if r_now > r_old * 1.1:
                score += 4
                trend = "개선"
            elif r_now < r_old * 0.9:
                score += 1   # 악화도 최소 1점 (데이터 있다는 것 자체에 신뢰)
                trend = "악화"
            else:
                score += 2
                trend = "안정"
        else:
            score += 2  # 비교 데이터 부족 → 중립 2점

    return min(30, max(0, score)), trend


def _income_score(per: float | None, div_yield: float | None) -> int:
    """이익수익률·배당 점수 (0~20)."""
    score = 0

    # 이익수익률 = 1/PER (Earnings Yield) — 채권 대비 매력도
    if per and per > 0:
        ey = 1 / per * 100   # %
        if ey >= 12:
            score += 12
        elif ey >= 8:
            score += 10
        elif ey >= 6:
            score += 7
        elif ey >= 4:
            score += 4
        else:
            score += 1

    # 배당수익률
    if div_yield:
        if div_yield >= 4:
            score += 8
        elif div_yield >= 3:
            score += 6
        elif div_yield >= 2:
            score += 4
        elif div_yield >= 1:
            score += 2

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

    try:
        data = _fetch_main(code)
    except Exception as e:
        report.error = f"수집 실패: {e}"
        return report

    report.per = data.get("per")
    report.sector_per = data.get("sector_per")
    report.pbr = data.get("pbr")
    report.eps = data.get("eps")
    report.div_yield = data.get("div_yield")
    report.roe_quarters = data.get("roe_quarters", [])

    # 전체 분기 중앙값 → 사이클 영향 완화, 최근 4분기 평균을 표시용으로 병행
    all_q = [v for v in report.roe_quarters if v is not None]
    q4    = [v for v in report.roe_quarters[:4] if v is not None]
    # 스코어링엔 중앙값(사이클 안정), 표시엔 최근 4분기 평균
    if all_q:
        from statistics import median as _median
        report.roe = round(_median(all_q), 2)
    elif q4:
        report.roe = round(mean(q4), 2)

    # 데이터 품질 판단
    has_per = report.per is not None
    has_pbr = report.pbr is not None
    has_roe = report.roe is not None
    if has_per and has_pbr and has_roe:
        report.data_quality = "완전"
    elif has_per or has_pbr:
        report.data_quality = "부분"
    else:
        report.data_quality = "기본"

    # 스코어링
    v = _valuation_score(report.per, report.pbr, report.sector_per, report.roe)
    q, report.roe_trend = _quality_score(report.roe, report.roe_quarters)
    i = _income_score(report.per, report.div_yield)

    report.valuation_score = v
    report.quality_score = q
    report.income_score = i
    report.fund_score = v + q + i
    report.fund_grade = _grade(report.fund_score)

    # 요약
    parts = []
    if report.per:
        parts.append(f"PER {report.per:.1f}배")
        if report.sector_per:
            rel = (report.sector_per - report.per) / report.sector_per * 100
            parts[-1] += f" (업종 {report.sector_per:.1f}배 대비 {rel:+.0f}%)"
    if report.pbr:
        parts.append(f"PBR {report.pbr:.2f}배")
    if report.roe:
        parts.append(f"ROE {report.roe:.1f}% ({report.roe_trend})")
    if report.div_yield:
        parts.append(f"배당 {report.div_yield:.2f}%")

    report.summary = (
        f"펀더멘탈 {report.fund_score}점/{report.fund_grade} "
        f"(밸류 {v}/50 · 품질 {q}/30 · 배당·이익 {i}/20). "
        f"[{report.data_quality}] " + ", ".join(parts) + "."
    )
    return report

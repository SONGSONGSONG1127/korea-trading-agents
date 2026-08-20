# -*- coding: utf-8 -*-
"""
DART(금융감독원 전자공시) OpenAPI 연동

무료 API 키 발급: https://opendart.fss.or.kr/uat/uia/easyLogin.do
발급 후 .streamlit/secrets.toml 에 아래 한 줄 추가:
    DART_API_KEY = "YOUR_KEY_HERE"

수집 지표:
    매출액, 영업이익, 당기순이익, 자산총계, 부채총계, 자본총계,
    영업활동현금흐름, 이자비용 (당기/전기 2개년)

계산 지표:
    ROA, 부채비율, 영업이익률, 매출성장률, 영업이익성장률, 이자보상배율
    Piotroski F-Score (9개 이진 지표)
"""
from __future__ import annotations

import io
import json
import os
import re
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import requests

DART_BASE = "https://opendart.fss.or.kr/api"
CACHE_DIR  = Path.home() / ".korea-trading-agents"
CODE_CACHE = CACHE_DIR / "dart_corp_codes.json"
DATA_CACHE = CACHE_DIR / "dart_financials"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/126.0 Safari/537.36"
    )
}


@dataclass
class DartFinancials:
    """당기 + 전기 재무 수치 및 파생 지표."""
    # 당기 (단위: 억원)
    revenue:       float | None = None
    op_profit:     float | None = None
    net_income:    float | None = None
    total_assets:  float | None = None
    total_liab:    float | None = None
    equity:        float | None = None
    operating_cf:  float | None = None
    interest_exp:  float | None = None

    # 전기
    prev_revenue:      float | None = None
    prev_op_profit:    float | None = None
    prev_net_income:   float | None = None
    prev_total_assets: float | None = None
    prev_total_liab:   float | None = None
    prev_equity:       float | None = None

    # 파생 지표
    roa:              float | None = None   # %
    debt_ratio:       float | None = None   # %
    op_margin:        float | None = None   # %
    revenue_growth:   float | None = None   # % YoY
    op_profit_growth: float | None = None   # % YoY
    interest_coverage: float | None = None  # 배

    # Piotroski F-Score
    f_score:    int = 0
    f_details:  list[str] = field(default_factory=list)  # 각 지표 설명


# ── 유틸 ─────────────────────────────────────────────────────────────────

def _api_key() -> str:
    key = os.environ.get("DART_API_KEY", "")
    if not key:
        try:
            import streamlit as st
            key = st.secrets.get("DART_API_KEY", "")  # type: ignore[arg-type]
        except Exception:
            pass
    return key


def _amt(s: str | None) -> float | None:
    """문자열 금액 → 억원 float. DART 단위는 원(KRW)."""
    if not s or s.strip() in ("", "-", "N/A"):
        return None
    try:
        return float(s.replace(",", "")) / 1e8   # 원 → 억원
    except ValueError:
        return None


# ── 기업코드 매핑 ─────────────────────────────────────────────────────────

def _load_corp_codes() -> dict[str, str]:
    """stock_code(6자리) → corp_code(8자리) 매핑 (7일 캐시)."""
    if CODE_CACHE.exists() and time.time() - CODE_CACHE.stat().st_mtime < 86400 * 7:
        return json.loads(CODE_CACHE.read_text(encoding="utf-8"))

    ak = _api_key()
    if not ak:
        return {}

    resp = requests.get(
        f"{DART_BASE}/corpCode.xml",
        params={"crtfc_key": ak},
        headers=HEADERS,
        timeout=60,
    )
    resp.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        xml = zf.read(zf.namelist()[0]).decode("utf-8")

    # XML 파서로 정확히 매핑 (정규식은 레코드 경계를 넘어 오매핑 발생)
    import xml.etree.ElementTree as ET
    root = ET.fromstring(xml)
    mapping: dict[str, str] = {}
    for item in root.findall(".//list"):
        corp_code  = (item.findtext("corp_code")  or "").strip()
        stock_code = (item.findtext("stock_code") or "").strip()
        if len(corp_code) == 8 and len(stock_code) == 6 and stock_code.isdigit():
            mapping[stock_code] = corp_code

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    CODE_CACHE.write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")
    return mapping


def get_corp_code(stock_code: str) -> str | None:
    return _load_corp_codes().get(stock_code.zfill(6))


# ── 재무제표 수집 ─────────────────────────────────────────────────────────

def _fetch_fnltt(corp_code: str, year: int, fs_div: str = "CFS") -> list[dict]:
    """DART fnlttSinglAcntAll — 연간 재무제표 행 목록."""
    ak = _api_key()
    resp = requests.get(
        f"{DART_BASE}/fnlttSinglAcntAll.json",
        params={
            "crtfc_key": ak,
            "corp_code": corp_code,
            "bsns_year": str(year),
            "reprt_code": "11011",   # 사업보고서
            "fs_div": fs_div,
        },
        headers=HEADERS,
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("list", []) if data.get("status") == "000" else []


def _parse_rows(rows: list[dict]) -> dict[str, tuple[float | None, float | None]]:
    """
    계정명 키워드 → (당기, 전기) 금액 dict.
    우선순위가 높은 계정명이 먼저 매칭되도록 순서 주의.
    """
    TARGET = [
        ("revenue",      ["매출액", "영업수익", "수익(매출액)"]),
        ("op_profit",    ["영업이익"]),
        ("net_income",   ["당기순이익"]),
        ("total_assets", ["자산총계"]),
        ("total_liab",   ["부채총계"]),
        ("equity",       ["자본총계"]),
        ("operating_cf", ["영업활동현금흐름", "영업활동으로 인한 현금흐름"]),
        ("interest_exp", ["이자비용"]),
    ]
    result: dict[str, tuple[float | None, float | None]] = {}
    for row in rows:
        nm = row.get("account_nm", "")
        for key, keywords in TARGET:
            if key in result:
                continue
            if any(kw in nm for kw in keywords):
                result[key] = (
                    _amt(row.get("thstrm_amount")),   # 당기
                    _amt(row.get("frmtrm_amount")),   # 전기
                )
    return result


def _calc_derived(fin: DartFinancials) -> None:
    """파생 지표 계산."""
    # ROA
    if fin.net_income is not None and fin.total_assets and fin.total_assets != 0:
        fin.roa = round(fin.net_income / fin.total_assets * 100, 2)

    # 부채비율
    if fin.total_liab is not None and fin.equity and fin.equity != 0:
        fin.debt_ratio = round(fin.total_liab / fin.equity * 100, 1)

    # 영업이익률
    if fin.op_profit is not None and fin.revenue and fin.revenue != 0:
        fin.op_margin = round(fin.op_profit / fin.revenue * 100, 1)

    # 매출 성장률 YoY
    if fin.revenue is not None and fin.prev_revenue and fin.prev_revenue != 0:
        fin.revenue_growth = round(
            (fin.revenue - fin.prev_revenue) / abs(fin.prev_revenue) * 100, 1
        )

    # 영업이익 성장률 YoY
    if fin.op_profit is not None and fin.prev_op_profit and fin.prev_op_profit != 0:
        fin.op_profit_growth = round(
            (fin.op_profit - fin.prev_op_profit) / abs(fin.prev_op_profit) * 100, 1
        )

    # 이자보상배율
    if fin.op_profit is not None and fin.interest_exp and fin.interest_exp > 0:
        fin.interest_coverage = round(fin.op_profit / fin.interest_exp, 1)


def _piotroski(fin: DartFinancials) -> tuple[int, list[str]]:
    """
    Piotroski F-Score (0~9).
    Piotroski (2000, JAR): 수익성 4 + 재무건전성 3 + 운영효율 2.
    """
    score = 0
    details: list[str] = []

    def check(cond: bool | None, label: str) -> None:
        nonlocal score
        if cond is None:
            details.append(f"⚪ {label} (데이터 없음)")
            return
        if cond:
            score += 1
            details.append(f"✅ {label}")
        else:
            details.append(f"❌ {label}")

    # ─ 수익성 (4) ─────────────────────────────────────────
    prev_roa = None
    if fin.prev_net_income is not None and fin.prev_total_assets and fin.prev_total_assets != 0:
        prev_roa = fin.prev_net_income / fin.prev_total_assets * 100

    check(fin.roa is not None and fin.roa > 0,         "F1. ROA 양수 (수익성)")
    check(fin.operating_cf is not None and fin.operating_cf > 0, "F2. 영업현금흐름 양수")
    if fin.roa is not None and prev_roa is not None:
        check(fin.roa > prev_roa,                      "F3. ROA 전년 대비 개선")
    else:
        check(None,                                    "F3. ROA 전년 대비 개선")
    # F4: 발생주의 — 현금이익 > 회계이익
    if fin.operating_cf is not None and fin.net_income is not None and fin.total_assets:
        accrual_cf   = fin.operating_cf / fin.total_assets
        accrual_ni   = fin.net_income   / fin.total_assets
        check(accrual_cf > accrual_ni,                 "F4. 현금이익 > 회계이익 (발생주의)")
    else:
        check(None,                                    "F4. 현금이익 > 회계이익 (발생주의)")

    # ─ 재무건전성 (3) ──────────────────────────────────────
    prev_dr = None
    if fin.prev_total_liab is not None and fin.prev_equity and fin.prev_equity != 0:
        prev_dr = fin.prev_total_liab / fin.prev_equity

    curr_dr = (fin.total_liab / fin.equity) if (fin.total_liab is not None and fin.equity and fin.equity != 0) else None

    if curr_dr is not None and prev_dr is not None:
        check(curr_dr < prev_dr,                       "F5. 부채비율 감소 (레버리지↓)")
    else:
        check(None,                                    "F5. 부채비율 감소 (레버리지↓)")

    # F6: 유동성 — equity/assets 비율 증가 (유동비율 대체)
    curr_ea = (fin.equity / fin.total_assets) if (fin.equity and fin.total_assets) else None
    prev_ea = (fin.prev_equity / fin.prev_total_assets) if (fin.prev_equity and fin.prev_total_assets) else None
    if curr_ea is not None and prev_ea is not None:
        check(curr_ea > prev_ea,                       "F6. 자본비율 개선 (유동성 대리)")
    else:
        check(None,                                    "F6. 자본비율 개선 (유동성 대리)")

    # F7: 주식수 증가 없음 — equity 순증가가 순이익 이하면 희석 없음
    if fin.equity is not None and fin.prev_equity is not None and fin.net_income is not None:
        eq_delta = fin.equity - fin.prev_equity
        check(eq_delta <= fin.net_income * 1.1,        "F7. 신주 희석 없음")
    else:
        check(None,                                    "F7. 신주 희석 없음")

    # ─ 운영효율 (2) ────────────────────────────────────────
    prev_om = None
    if fin.prev_op_profit is not None and fin.prev_revenue and fin.prev_revenue != 0:
        prev_om = fin.prev_op_profit / fin.prev_revenue * 100

    if fin.op_margin is not None and prev_om is not None:
        check(fin.op_margin > prev_om,                 "F8. 영업이익률 개선")
    else:
        check(None,                                    "F8. 영업이익률 개선")

    # F9: 자산회전율 개선
    curr_ato = (fin.revenue / fin.total_assets) if (fin.revenue and fin.total_assets) else None
    prev_ato = (fin.prev_revenue / fin.prev_total_assets) if (fin.prev_revenue and fin.prev_total_assets) else None
    if curr_ato is not None and prev_ato is not None:
        check(curr_ato > prev_ato,                     "F9. 자산회전율 개선")
    else:
        check(None,                                    "F9. 자산회전율 개선")

    return score, details


# ── 캐시 래퍼 ─────────────────────────────────────────────────────────────

def _cache_path(stock_code: str, year: int) -> Path:
    return DATA_CACHE / f"{stock_code}_{year}.json"


def get_financials(stock_code: str, year: int | None = None) -> DartFinancials | None:
    """
    DART에서 연간 재무제표 수집 → DartFinancials 반환.
    API 키 없거나 종목 미등록이면 None.
    결과는 24시간 파일 캐시.
    """
    if not _api_key():
        return None

    from datetime import date
    if year is None:
        today = date.today()
        # 사업보고서는 3~4월에 제출 → 4월 이전이면 2년 전 연도 사용
        year = today.year - 1 if today.month >= 4 else today.year - 2

    cache_file = _cache_path(stock_code, year)
    if cache_file.exists() and time.time() - cache_file.stat().st_mtime < 86400:
        try:
            raw = json.loads(cache_file.read_text(encoding="utf-8"))
            fin = DartFinancials(**{k: v for k, v in raw.items() if k != "f_details"})
            fin.f_details = raw.get("f_details", [])
            return fin
        except Exception:
            pass

    corp_code = get_corp_code(stock_code)
    if not corp_code:
        return None

    # 연결 우선, 실패 시 별도
    rows = _fetch_fnltt(corp_code, year, "CFS")
    if not rows:
        rows = _fetch_fnltt(corp_code, year, "OFS")
    if not rows:
        return None

    parsed = _parse_rows(rows)

    fin = DartFinancials()
    for field_name, (curr, prev) in parsed.items():
        setattr(fin, field_name, curr)
        prev_field = f"prev_{field_name}"
        if hasattr(fin, prev_field):
            setattr(fin, prev_field, prev)

    _calc_derived(fin)
    fin.f_score, fin.f_details = _piotroski(fin)

    # 캐시 저장
    DATA_CACHE.mkdir(parents=True, exist_ok=True)
    import dataclasses
    cache_file.write_text(
        json.dumps(dataclasses.asdict(fin), ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    return fin

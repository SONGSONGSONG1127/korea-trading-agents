# -*- coding: utf-8 -*-
"""
종목명 검색 (국장·미장 공통)

네이버 주식 자동완성 API(공개 JSON) 사용 — 한글/영문 이름·부분 입력을
종목코드(KR) 또는 티커(US)로 변환한다.
  "삼성전"   → 삼성전자(005930), 삼성전기(009150), ...
  "팔란티어" → PLTR (NASDAQ)
"""
from __future__ import annotations

import re

import requests

_HEADERS = {"User-Agent": "Mozilla/5.0"}
_URL = "https://ac.stock.naver.com/ac"


def search(query: str, market: str = "KR", limit: int = 5) -> list[dict]:
    """이름/코드 부분 검색 → [{name, code, exchange}] (market: "KR" | "US")."""
    try:
        r = requests.get(_URL, params={"q": query, "target": "stock"},
                         headers=_HEADERS, timeout=8)
        items = r.json().get("items", [])
    except Exception:
        return []

    out: list[dict] = []
    for it in items:
        code = str(it.get("code", ""))
        name = str(it.get("name", ""))
        nation = it.get("nationCode", "")
        exch = it.get("typeCode", "")
        if market == "KR":
            if nation == "KOR" and re.fullmatch(r"\d{6}", code):
                out.append({"name": name, "code": code, "exchange": exch})
        else:
            if nation == "USA" and code:
                out.append({"name": name, "code": code, "exchange": exch})
        if len(out) >= limit:
            break
    return out

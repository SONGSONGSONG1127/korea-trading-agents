# -*- coding: utf-8 -*-
"""
할인찬스 파인더 — 저평가 가치주 스크리너

컨셉 (근거 문헌):
  Piotroski (2000)  : "싼 주식 중 재무가 건강한 것만" — 저PBR × 고F-Score 조합이
                      가치함정(value trap)을 걸러내며 초과수익
  Fama-French (1992): 밸류 팩터 — 저PBR 주식의 장기 초과수익
  Damodaran (2012)  : 절대 PER보다 업종 상대 할인이 중요

2단계 깔때기:
  1단계 (싼 필터) : 네이버 시총 상위 페이지의 PER 컬럼으로 저PER 후보 추출 (요청 10번)
  2단계 (정밀 분석): 후보별 fundamental_agent(PER·PBR·업종상대·ROE·배당·DART F-Score)
                    + 가격 데이터(52주 고점 할인율, 바닥 안정화)

할인점수 (0~100):
  밸류에이션 (0~50): fundamental_agent 재사용 — PER 절대+업종상대, PBR vs 고든모형 적정가
  품질       (0~30): fundamental_agent 재사용 — ROE + Piotroski F-Score (가치함정 방지)
  안전마진   (0~20): 52주 고점 대비 할인폭 (0~10) + 바닥 안정화/기술 상태 (0~10)

제외 규칙 (표시와 함께):
  - 적자 (PER ≤ 0)                     → 1단계에서 제외
  - F-Score ≤ 3                        → 가치함정 위험
  - 기술점수 < -0.3                    → 하락 추세 지속 (낙하는 칼)
"""
from __future__ import annotations

import re
import time
from typing import Callable

import pandas as pd
import requests
from bs4 import BeautifulSoup

from . import backtest, fundamental_agent, technical_agent

HEADERS = technical_agent.HEADERS
ProgressCb = Callable[[int, int, str], None] | None

_ETF_WORDS = ("KODEX", "TIGER", "ACE ", "SOL ", "PLUS ", "RISE ", "KIWOOM",
              "HANARO", "ARIRANG", "KOSEF", "액티브", "레버리지", "인버스",
              "ETN", "채권", "선물", "TOP10", "나스닥", "S&P")


def _market_pool_with_per(kospi_pages: int = 6, kosdaq_pages: int = 4) -> list[dict]:
    """시총 상위 페이지에서 종목 + 페이지 PER 컬럼 수집 (시총 순 유지)."""
    out: list[dict] = []
    seen: set[str] = set()
    jobs = [(0, p) for p in range(1, kospi_pages + 1)] + [(1, p) for p in range(1, kosdaq_pages + 1)]
    for sosok, page in jobs:
        url = f"https://finance.naver.com/sise/sise_market_sum.naver?sosok={sosok}&page={page}"
        res = requests.get(url, headers=HEADERS, timeout=10)
        res.raise_for_status()
        res.encoding = "euc-kr"
        soup = BeautifulSoup(res.text, "lxml")
        for row in soup.select("table.type_2 tr"):
            a = row.select_one("a.tltle")
            if not a:
                continue
            m = re.search(r"code=(\d{6})", a.get("href", ""))
            if not m:
                continue
            code = m.group(1)
            name = a.get_text(strip=True)
            if code in seen or not code.endswith("0") or "스팩" in name:
                continue
            if any(w in name for w in _ETF_WORDS):
                continue
            tds = [td.get_text(strip=True).replace(",", "") for td in row.select("td")]
            # 컬럼: N,종목명,현재가,전일비,등락률,액면가,시가총액,상장주식수,외국인비율,거래량,PER,ROE
            try:
                price = float(tds[2])
            except (IndexError, ValueError):
                continue
            if price < 1000:
                continue
            def _f(i: int) -> float | None:
                try:
                    return float(tds[i])
                except (IndexError, ValueError):
                    return None
            out.append({"code": code, "name": name, "price": price,
                        "per": _f(10), "roe": _f(11)})
            seen.add(code)
        time.sleep(0.05)
    return out


def _margin_of_safety(df: pd.DataFrame) -> tuple[int, float, float, str]:
    """안전마진 점수 (0~20), 52주 할인율, 기술점수, 근거 문자열."""
    close = float(df["close"].iloc[-1])
    hi52 = float(df["close"].tail(252).max())
    discount = close / hi52 - 1  # 음수 = 고점 대비 하락

    enriched = technical_agent.enrich(df.copy())
    raw = backtest.score_series(enriched).iloc[-1]
    tech = float(raw) if not pd.isna(raw) else 0.0
    ma20 = float(enriched["ma20"].iloc[-1]) if not pd.isna(enriched["ma20"].iloc[-1]) else close

    # ① 할인폭 (0~10): 고점 대비 많이 빠져 있을수록 (지나친 폭락 -60%↓는 가산 중단)
    d = -discount
    if d >= 0.40:
        disc_pts = 10
    elif d >= 0.30:
        disc_pts = 8
    elif d >= 0.20:
        disc_pts = 5
    elif d >= 0.10:
        disc_pts = 2
    else:
        disc_pts = 0

    # ② 바닥 안정화 (0~10): 하락이 멈췄다는 신호
    stab_pts = 0
    if tech >= 0.1:
        stab_pts += 6
    elif tech >= -0.1:
        stab_pts += 4
    if close >= ma20:
        stab_pts += 4

    reason = f"52주 고점 대비 {discount:+.0%}, 기술점수 {tech * 100:+.0f}"
    reason += " (20일선 위)" if close >= ma20 else " (20일선 아래)"
    return disc_pts + stab_pts, discount, tech, reason


def run(
    per_max: float = 15.0,
    n_scan: int = 40,
    n_top: int = 20,
    progress: ProgressCb = None,
) -> dict:
    """
    per_max : 1단계 저PER 컷 (페이지 PER 기준)
    n_scan  : 2단계 정밀 분석 종목 수 (종목당 요청 ~4번, 느림)
    n_top   : 최종 표시 수
    """
    pool = _market_pool_with_per()

    # ── 1단계: 흑자 + 저PER 후보 (PER 오름차순) ───────────────────────────
    cheap = [p for p in pool if p["per"] is not None and 0 < p["per"] <= per_max]
    cheap.sort(key=lambda p: p["per"])
    candidates = cheap[:n_scan]

    rows: list[dict] = []
    excluded: list[dict] = []

    for i, cand in enumerate(candidates, 1):
        if progress:
            progress(i, len(candidates), f"{cand['name']}({cand['code']})")
        try:
            rep = fundamental_agent.run(cand["code"], stock_name=cand["name"])
            if rep.error:
                continue
            df = technical_agent.fetch_daily_prices_fast(cand["code"], days=420)
            if len(df) < 120:
                continue
            margin, discount52, tech, margin_reason = _margin_of_safety(df)

            # ── 제외 규칙 (가치함정 · 낙하는 칼) ──────────────────────────
            if rep.f_score is not None and rep.f_score <= 3:
                excluded.append({"name": cand["name"], "code": cand["code"],
                                 "reason": f"F-Score {rep.f_score}/9 — 재무 악화, 가치함정 위험"})
                continue
            if tech < -0.3:
                excluded.append({"name": cand["name"], "code": cand["code"],
                                 "reason": f"기술점수 {tech * 100:+.0f} — 하락 추세 지속 중 (낙하는 칼)"})
                continue

            score = rep.valuation_score + rep.quality_score + margin
            rows.append({
                "code":          cand["code"],
                "name":          cand["name"],
                "score":         int(score),
                "val_score":     rep.valuation_score,
                "qual_score":    rep.quality_score,
                "margin_score":  margin,
                "per":           rep.per,
                "sector_per":    rep.sector_per,
                "pbr":           rep.pbr,
                "roe":           rep.roe,
                "div_yield":     rep.div_yield,
                "f_score":       rep.f_score,
                "f_details":     rep.f_details,
                "debt_ratio":    rep.debt_ratio,
                "discount52":    discount52,
                "tech":          tech,
                "margin_reason": margin_reason,
                "summary":       rep.summary,
                "narrative":     fundamental_agent.narrative(rep),
                "data_quality":  rep.data_quality,
                "close":         float(df["close"].iloc[-1]),
            })
        except Exception:
            continue
        time.sleep(0.1)

    rows.sort(key=lambda r: -r["score"])
    return {
        "rows":       rows[:n_top],
        "excluded":   excluded,
        "n_pool":     len(pool),
        "n_cheap":    len(cheap),
        "n_scanned":  len(candidates),
        "per_max":    per_max,
        "run_date":   str(pd.Timestamp.now().date()),
    }

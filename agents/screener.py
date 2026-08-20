# -*- coding: utf-8 -*-
"""
오늘의 후보 종목 스크리너 — 2단계 깔때기

1단계 (싼 필터): 네이버 시가총액 상위 목록에서 거래대금(현재가×거래량) 상위 N종목을 뽑고,
                차트 JSON API(종목당 요청 1번)로 일봉을 받아 근사 기술 점수를 계산
2단계 (풀 분석): 1단계 상위 K종목만 뉴스 수집 포함 3-에이전트 파이프라인 실행,
                종합 점수 순으로 랭킹

토큰(LLM) 비용은 0 — 전부 로컬 규칙 기반 계산이며, 비용은 HTTP 요청 수뿐이다.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from io import StringIO
from typing import Callable

import pandas as pd
import requests
from bs4 import BeautifulSoup

from . import backtest, community, fundamental_agent, news_agent, strategy_agent, technical_agent

HEADERS = technical_agent.HEADERS

ProgressCb = Callable[[int, int, str], None] | None


@dataclass
class Candidate:
    code: str
    name: str = ""
    close: float = 0.0
    value: float = 0.0            # 거래대금 근사 (현재가 × 거래량)
    quick_score: float = 0.0      # 1단계 근사 기술 점수
    df: pd.DataFrame | None = None
    news: object = None
    tech: object = None
    strat: object = None
    fund: object = None           # fundamental_agent.FundamentalReport
    comm: object = None           # community.Buzz (참고용)


def _market_leaders(kospi_pages: int = 4, kosdaq_pages: int = 3) -> list[Candidate]:
    """시가총액 상위 페이지(코스피/코스닥)에서 종목 목록을 모은다. 페이지당 50종목."""
    out: dict[str, Candidate] = {}
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
            # 우선주(끝자리 0 아님)·스팩·ETF/ETN·동전주 제외
            if not code.endswith("0") or "스팩" in name:
                continue
            etf_words = ("KODEX", "TIGER", "ACE ", "SOL ", "PLUS ", "RISE ", "KIWOOM",
                         "HANARO", "ARIRANG", "KOSEF", "액티브", "레버리지", "인버스",
                         "ETN", "채권", "선물", "TOP10", "나스닥", "S&P")
            if any(w in name for w in etf_words):
                continue
            tds = [td.get_text(strip=True).replace(",", "") for td in row.select("td")]
            nums = [float(t) for t in tds if re.fullmatch(r"\d+(\.\d+)?", t)]
            # 기본 컬럼 순서: N, 현재가, 전일비, 액면가, 시총, 상장주식수, 외인비율, 거래량, PER, ROE
            try:
                price = float(tds[2])
                volume = float(tds[9])
            except (IndexError, ValueError):
                if len(nums) < 3:
                    continue
                price, volume = nums[1], nums[-3]
            if price < 1000:
                continue
            out[code] = Candidate(code=code, name=name, close=price, value=price * volume)
    return sorted(out.values(), key=lambda c: -c.value)


def stage1(liquidity_top: int = 200, progress: ProgressCb = None) -> list[Candidate]:
    """거래대금 상위 종목에 대해 근사 기술 점수를 계산해 내림차순 정렬로 반환."""
    leaders = _market_leaders()[:liquidity_top]
    scored: list[Candidate] = []
    for i, cand in enumerate(leaders, 1):
        if progress:
            progress(i, len(leaders), f"{cand.name}({cand.code})")
        try:
            df = technical_agent.fetch_daily_prices_fast(cand.code, days=160)
            if len(df) < 70:
                continue
            enriched = technical_agent.enrich(df.copy())
            q = backtest.score_series(enriched).iloc[-1]
            if pd.isna(q):
                continue
            cand.quick_score = round(float(q), 3)
            cand.close = float(df["close"].iloc[-1])
            cand.df = df
            scored.append(cand)
        except Exception:
            continue
        time.sleep(0.05)  # 네이버 부하 완화
    scored.sort(key=lambda c: -c.quick_score)
    return scored


def stage2(cands: list[Candidate], full_top: int = 10, progress: ProgressCb = None) -> list[Candidate]:
    """1단계 상위 K종목만 뉴스 포함 풀 파이프라인 실행, 종합 점수 순 정렬."""
    try:
        regime = technical_agent.fetch_market_regime("KOSPI")
    except Exception:
        regime = None
    try:
        top_search = community.top_searched()
    except Exception:
        top_search = []

    top = cands[:full_top]
    for i, cand in enumerate(top, 1):
        if progress:
            progress(i, len(top), f"{cand.name}({cand.code})")
        try:
            cand.news = news_agent.run(cand.code, news_pages=2)
            cand.tech = technical_agent.run(cand.code, df=cand.df, regime=regime)
            try:
                cand.fund = fundamental_agent.run(cand.code, stock_name=cand.name)
            except Exception:
                cand.fund = None
            cand.strat = strategy_agent.run(cand.news, cand.tech, cand.fund)
            cand.comm = community.get(cand.code, top=top_search)
            if cand.news.stock_name != cand.code:
                cand.name = cand.news.stock_name
        except Exception:
            cand.strat = None

    # 펀더멘탈 위험(fund_score < 32) 종목 제거
    done = [
        c for c in top
        if c.strat is not None and not c.strat.error
        and not (c.fund and not c.fund.error and c.fund.fund_score < 32)
    ]
    done.sort(key=lambda c: -c.strat.combined_score)
    return done

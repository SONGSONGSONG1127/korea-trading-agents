# -*- coding: utf-8 -*-
"""
미국 시장 데이터 레이어 (USA-TradingAgents)

yfinance 기반 — 스크래핑 없이 안정적인 공식 API 경유.
K-Trading의 technical_agent와 동일한 df 스키마(date/open/high/low/close/volume)를
반환하므로 enrich()·score_series()·backtest 등 기존 엔진을 그대로 재사용한다.

유니버스: S&P500 구성종목 (위키피디아, 하루 캐시)
벤치마크: ^GSPC (S&P500 지수)
"""
from __future__ import annotations

import time
from io import StringIO

import pandas as pd
import requests

_WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
_HEADERS = {"User-Agent": "Mozilla/5.0"}

_universe_cache: dict = {"date": None, "data": None}


def sp500_universe() -> list[dict]:
    """S&P500 구성종목 [{code, name, sector}] (하루 캐시)."""
    today = str(pd.Timestamp.now().date())
    if _universe_cache["date"] == today and _universe_cache["data"]:
        return _universe_cache["data"]
    res = requests.get(_WIKI_URL, headers=_HEADERS, timeout=15)
    res.raise_for_status()
    tbl = pd.read_html(StringIO(res.text))[0]
    out = []
    for _, r in tbl.iterrows():
        ticker = str(r["Symbol"]).replace(".", "-")  # BRK.B → BRK-B (yfinance 표기)
        out.append({"code": ticker, "name": str(r["Security"]),
                    "sector": str(r["GICS Sector"])})
    _universe_cache.update(date=today, data=out)
    return out


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """yfinance 결과를 K-Trading 표준 스키마로 변환."""
    df = df.reset_index()
    df.columns = [str(c[0] if isinstance(c, tuple) else c).lower() for c in df.columns]
    df = df.rename(columns={"index": "date", "datetime": "date"})
    df = df[["date", "open", "high", "low", "close", "volume"]].copy()
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
    df = df.dropna(subset=["close"])
    return df.drop_duplicates(subset="date").sort_values("date").reset_index(drop=True)


def fetch_daily_prices(ticker: str, days: int = 260) -> pd.DataFrame:
    """단일 종목 일봉 (auto-adjusted)."""
    import yfinance as yf
    period = f"{max(int(days * 1.1), 30)}d"
    df = yf.Ticker(ticker).history(period=period, interval="1d", auto_adjust=True)
    if df.empty:
        raise ValueError(f"{ticker}: 데이터 없음")
    return _normalize(df)


def fetch_many(tickers: list[str], days: int = 1200,
               progress=None, chunk: int = 100) -> dict[str, pd.DataFrame]:
    """여러 종목 일봉 일괄 다운로드 (한 요청에 chunk개씩 스레드 다운로드)."""
    import yfinance as yf
    period = f"{max(int(days * 1.1), 30)}d"
    out: dict[str, pd.DataFrame] = {}
    for i in range(0, len(tickers), chunk):
        batch = tickers[i:i + chunk]
        if progress:
            progress(min(i + chunk, len(tickers)), len(tickers),
                     f"{batch[0]} 외 {len(batch) - 1}종목")
        raw = yf.download(batch, period=period, interval="1d", auto_adjust=True,
                          group_by="ticker", threads=True, progress=False)
        for t in batch:
            try:
                sub = raw[t] if len(batch) > 1 else raw
                sub = sub.dropna(subset=["Close"])
                if len(sub) < 30:
                    continue
                out[t] = _normalize(sub)
            except (KeyError, TypeError):
                continue
        time.sleep(0.3)
    return out


def fetch_benchmark(days: int = 1200) -> pd.DataFrame:
    """S&P500 지수 (^GSPC) 일봉."""
    return fetch_daily_prices("^GSPC", days=days)


# ── 종목 기본 정보 ────────────────────────────────────────────────────────

SECTOR_KR = {
    "Information Technology": "IT",
    "Health Care":            "헬스케어",
    "Financials":             "금융",
    "Consumer Discretionary": "임의소비재",
    "Communication Services": "커뮤니케이션",
    "Industrials":            "산업재",
    "Consumer Staples":       "필수소비재",
    "Energy":                 "에너지",
    "Utilities":              "유틸리티",
    "Real Estate":            "부동산",
    "Materials":              "소재",
    "Technology":             "IT",
    "Healthcare":             "헬스케어",
    "Financial Services":     "금융",
    "Consumer Cyclical":      "임의소비재",
    "Consumer Defensive":     "필수소비재",
    "Basic Materials":        "소재",
}


def sector_kr(sector: str) -> str:
    return SECTOR_KR.get(sector, sector)


def ticker_info(ticker: str) -> dict:
    """회사 기본 정보 (yfinance .info에서 필요한 것만)."""
    import yfinance as yf
    try:
        info = yf.Ticker(ticker).info or {}
    except Exception:
        info = {}
    return {
        "name":       info.get("longName") or info.get("shortName") or ticker,
        "sector":     info.get("sector") or "",
        "industry":   info.get("industry") or "",
        "market_cap": info.get("marketCap"),
        "per":        info.get("trailingPE"),
        "fwd_per":    info.get("forwardPE"),
        "hi52":       info.get("fiftyTwoWeekHigh"),
        "lo52":       info.get("fiftyTwoWeekLow"),
        "div_yield":  info.get("dividendYield"),   # 이미 % 값 (yfinance 0.2.5x)
        "summary":    info.get("longBusinessSummary") or "",
    }

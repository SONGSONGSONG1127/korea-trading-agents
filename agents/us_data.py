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


_NAME_SUFFIXES = (" Corporation", " Incorporated", " Inc.", " Inc", " Company",
                  " Co.", " Ltd.", " plc", " PLC", " Holdings", " Group",
                  " (Class A)", " (Class B)", " (Class C)")


def _clean_name(name: str) -> str:
    out = name
    for sfx in _NAME_SUFFIXES:
        if out.endswith(sfx):
            out = out[: -len(sfx)]
    return out.strip().rstrip(",")


def _wiki_title(query: str) -> str | None:
    """제목 후보 탐색: opensearch → 전문검색(list=search) 폴백."""
    r = requests.get(
        "https://ko.wikipedia.org/w/api.php",
        params={"action": "opensearch", "search": query, "limit": 1, "format": "json"},
        headers=_HEADERS, timeout=8,
    )
    titles = r.json()[1]
    if titles:
        return titles[0]
    r = requests.get(
        "https://ko.wikipedia.org/w/api.php",
        params={"action": "query", "list": "search", "srsearch": query,
                "srlimit": 1, "format": "json"},
        headers=_HEADERS, timeout=8,
    )
    hits = r.json().get("query", {}).get("search", [])
    return hits[0]["title"] if hits else None


def usd_krw() -> float | None:
    """현재 달러/원 환율 (yfinance USDKRW=X)."""
    try:
        df = fetch_daily_prices("USDKRW=X", days=7)
        return float(df["close"].iloc[-1])
    except Exception:
        return None


def dividends_since(ticker: str, since_date: str) -> float:
    """since_date 이후 주당 배당 합계 (USD). 실패 시 0."""
    import yfinance as yf
    try:
        divs = yf.Ticker(ticker).dividends
        if divs is None or len(divs) == 0:
            return 0.0
        idx = divs.index.tz_localize(None) if divs.index.tz is not None else divs.index
        cutoff = pd.Timestamp(since_date)
        return float(divs[idx >= cutoff].sum())
    except Exception:
        return 0.0


def wiki_summary_kr(name: str) -> str | None:
    """한국어 위키피디아에서 회사 요약 2~3문장 (LLM 무사용). 없으면 None."""
    from urllib.parse import quote
    try:
        title = _wiki_title(name)
        if not title and _clean_name(name) != name:
            title = _wiki_title(_clean_name(name))
        if not title:
            return None
        s = requests.get(
            f"https://ko.wikipedia.org/api/rest_v1/page/summary/{quote(title)}",
            headers=_HEADERS, timeout=8,
        ).json()
        extract = s.get("extract") or ""
        if len(extract) < 30:
            return None
        # 3문장까지만
        parts = extract.split(". ")
        return (". ".join(parts[:3]) + ("." if not parts[min(2, len(parts) - 1)].endswith(".") else "")).strip()
    except Exception:
        return None


def market_regime() -> tuple[str, str]:
    """S&P500(^GSPC) vs 60일선 — 시장 레짐 판정 (technical_agent와 동일 로직)."""
    try:
        df = fetch_benchmark(days=200)
        close = df["close"]
        ma60 = close.rolling(60).mean().iloc[-1]
        last = float(close.iloc[-1])
        if last > ma60 * 1.005:
            return "순풍", f"S&P500 {last:,.0f} > 60일선 {ma60:,.0f} (시장 상승 국면)"
        if last < ma60 * 0.995:
            return "역풍", f"S&P500 {last:,.0f} < 60일선 {ma60:,.0f} (시장 하락 국면)"
        return "중립", f"S&P500 {last:,.0f} ≈ 60일선 {ma60:,.0f}"
    except Exception:
        return "중립", "지수 데이터 수집 실패"


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

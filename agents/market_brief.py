# -*- coding: utf-8 -*-
"""
시황 브리프 — 지수 등락 + 네이버 증권 주요뉴스 + 이슈 키워드.

하락장이 '왜' 하락인지: 주요뉴스(category=mainnews)의 첫 기사들이 당일 시황 요약
기사라서 헤드라인+리드문이 그대로 하락/상승 이유가 된다. 여기에 지수 등락률과
헤드라인 빈출 키워드(이슈 태그)를 붙여 아침 브리핑·앱 메인·급락 알림에 쓴다.
"""
from __future__ import annotations

import html as _html
import re
from datetime import datetime

import requests

_NEWS_API = "https://m.stock.naver.com/api/news/list?category=mainnews&pageSize={n}&page=1"
_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/"}

# 이슈 키워드 추출용 불용어 (조사·일반어)
_STOP = {"코스피", "코스닥", "증시", "오늘", "국내", "시장", "주가", "종목", "지수",
         "개장", "마감", "장중", "특징주", "속보", "단독", "포인트", "선", "등", "속",
         "위협", "돌파", "하락", "상승", "급락", "급등", "약세", "강세", "출발", "눈"}


def _strip(t: str) -> str:
    return _html.unescape(re.sub(r"<[^>]+>", "", str(t))).strip()


def fetch_mainnews(n: int = 8) -> list[dict]:
    """주요뉴스 → [{title, lead, press, time, url}] (최신순)."""
    r = requests.get(_NEWS_API.format(n=n), headers=_HEADERS, timeout=10)
    r.raise_for_status()
    out = []
    for it in r.json():
        tit = _strip(it.get("tit", ""))
        if not tit:
            continue
        dt = str(it.get("dt", ""))
        try:
            t = datetime.strptime(dt[:12], "%Y%m%d%H%M").strftime("%m/%d %H:%M")
        except ValueError:
            t = ""
        out.append({
            "title": tit,
            "lead":  _strip(it.get("subcontent", "")),
            "press": str(it.get("ohnm", "")),
            "time":  t,
            "url":   f"https://n.news.naver.com/mnews/article/{it.get('oid')}/{it.get('aid')}",
        })
    return out


def index_moves(market: str = "KR") -> list[dict]:
    """지수 등락 → [{name, close, chg_pct}]. 장중이면 당일 실시간 캔들 반영."""
    out = []
    if market == "US":
        from . import us_data
        pairs = [("S&P500", "^GSPC"), ("나스닥", "^IXIC")]
        for name, code in pairs:
            try:
                df = us_data.fetch_daily_prices(code, days=10)
                c, p = float(df["close"].iloc[-1]), float(df["close"].iloc[-2])
                out.append({"name": name, "close": c, "chg_pct": (c / p - 1) * 100})
            except Exception:
                continue
        return out
    from . import technical_agent
    for name in ("KOSPI", "KOSDAQ"):
        try:
            df = technical_agent.fetch_daily_prices_fast(name, days=10)
            c, p = float(df["close"].iloc[-1]), float(df["close"].iloc[-2])
            out.append({"name": name, "close": c, "chg_pct": (c / p - 1) * 100})
        except Exception:
            continue
    return out


def keywords(articles: list[dict], k: int = 5) -> list[str]:
    """헤드라인 빈출 키워드 (이슈 태그) — 2자 이상 한글/영문 토큰 빈도."""
    freq: dict[str, int] = {}
    for a in articles:
        for tok in re.findall(r"[가-힣A-Za-z]{2,}", a["title"]):
            if tok in _STOP:
                continue
            freq[tok] = freq.get(tok, 0) + 1
    ranked = sorted(freq.items(), key=lambda x: -x[1])
    return [w for w, c in ranked if c >= 2][:k]


def build(market: str = "KR", n_news: int = 8) -> dict:
    """{indices, articles, keywords, asof} — 실패 항목은 빈 리스트."""
    try:
        idx = index_moves(market)
    except Exception:
        idx = []
    try:
        arts = fetch_mainnews(n_news) if market == "KR" else _us_news()
    except Exception:
        arts = []
    return {
        "indices":  idx,
        "articles": arts,
        "keywords": keywords(arts) if arts else [],
        "asof":     datetime.now().strftime("%m/%d %H:%M"),
    }


def _us_news(n: int = 4) -> list[dict]:
    """미장 헤드라인 (yfinance ^GSPC 뉴스) — 포맷은 mainnews와 동일 키."""
    import yfinance as yf
    out = []
    for it in (yf.Ticker("^GSPC").news or [])[:n]:
        c = it.get("content") or it
        tit = str(c.get("title", "")).strip()
        if not tit:
            continue
        out.append({"title": tit, "lead": str(c.get("summary", ""))[:200],
                    "press": str((c.get("provider") or {}).get("displayName", "")
                                 if isinstance(c.get("provider"), dict) else it.get("publisher", "")),
                    "time": "", "url": str((c.get("clickThroughUrl") or {}).get("url", "")
                                           if isinstance(c.get("clickThroughUrl"), dict) else it.get("link", ""))})
    return out


def telegram_section() -> str:
    """아침 브리핑용 시황 섹션 (KR 중심 + US 지수)."""
    lines = ["*📰 시황*"]
    b = build("KR", n_news=6)
    for i in b["indices"]:
        arrow = "🔻" if i["chg_pct"] < 0 else "🔺"
        lines.append(f"  {arrow} {i['name']} {i['close']:,.2f} ({i['chg_pct']:+.2f}%)")
    try:
        for i in index_moves("US"):
            arrow = "🔻" if i["chg_pct"] < 0 else "🔺"
            lines.append(f"  {arrow} {i['name']} {i['close']:,.2f} ({i['chg_pct']:+.2f}%)")
    except Exception:
        pass
    if b["keywords"]:
        lines.append(f"  🏷️ 이슈: {' · '.join(b['keywords'])}")
    for a in b["articles"][:4]:
        lines.append(f"  · [{a['title']}]({a['url']}) — {a['press']}")
    if b["articles"]:
        lines.append(f"  _{b['articles'][0]['lead'][:150]}_")
    return "\n".join(lines)

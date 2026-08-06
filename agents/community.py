# -*- coding: utf-8 -*-
"""
커뮤니티 관심 지표 (참고용 — 점수 미반영)

- 네이버 금융 검색상위 30: 시장 참여자들이 지금 무엇을 찾아보는지 (레딧 인기주 투표와 유사한 역할)
- 종목토론실 활성도: 오늘 게시글 수가 평소 대비 몇 배인지

관심(버즈)은 상승 재료일 수도, 급락 후 아우성일 수도 있어 방향성이 없다.
그래서 뉴스와 같은 원칙으로 점수에는 반영하지 않고 부수 정보로만 표시한다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

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
class SearchRank:
    rank: int
    code: str
    name: str
    ratio: str  # 검색비율 (예: "2.51%")


@dataclass
class Buzz:
    code: str
    search_rank: int | None = None      # 네이버 검색상위 순위 (30위 밖이면 None)
    search_ratio: str = ""
    board_today: int = 0                # 오늘 토론실 게시글 수 (측정 범위 내)
    board_daily_avg: float = 0.0        # 직전 일자들 하루 평균
    board_ratio: float | None = None    # 오늘/평소 배율 (None = 측정 상한 초과)
    note: str = ""


def _get(url: str) -> BeautifulSoup:
    res = requests.get(url, headers=HEADERS, timeout=10)
    res.raise_for_status()
    res.encoding = "euc-kr"
    return BeautifulSoup(res.text, "lxml")


def top_searched() -> list[SearchRank]:
    """네이버 금융 검색상위 30 종목."""
    soup = _get("https://finance.naver.com/sise/lastsearch2.naver")
    out: list[SearchRank] = []
    for row in soup.select("table.type_5 tr"):
        a = row.select_one("a.tltle")
        if not a:
            continue
        m = re.search(r"code=(\d{6})", a.get("href", ""))
        if not m:
            continue
        tds = [td.get_text(strip=True) for td in row.select("td")]
        ratio = next((t for t in tds if t.endswith("%") and "." in t), "")
        out.append(SearchRank(rank=len(out) + 1, code=m.group(1),
                              name=a.get_text(strip=True), ratio=ratio))
    return out


def board_buzz(code: str, pages: int = 3) -> tuple[int, float, float | None]:
    """종목토론실 게시글 활성도: (오늘 글 수, 직전 일자 하루 평균, 배율).
    배율 None = 측정 페이지가 전부 오늘 글이라 상한 초과(매우 활발)."""
    stamps: list[datetime] = []
    for page in range(1, pages + 1):
        soup = _get(f"https://finance.naver.com/item/board.naver?code={code}&page={page}")
        for span in soup.select("table.type2 tr td span.tah"):
            m = re.match(r"(\d{4})\.(\d{2})\.(\d{2}) (\d{2}):(\d{2})", span.get_text(strip=True))
            if m:
                stamps.append(datetime(*map(int, m.groups())))
    if not stamps:
        return 0, 0.0, 0.0

    today = datetime.now().date()
    per_day: dict = {}
    for ts in stamps:
        per_day[ts.date()] = per_day.get(ts.date(), 0) + 1

    today_n = per_day.pop(today, 0)
    if not per_day:
        return today_n, 0.0, None  # 전부 오늘 글 — 상한 초과
    # 가장 오래된 날짜는 페이지 경계에서 잘렸을 수 있으므로 평균에서 제외 (하루면 그대로 사용)
    if len(per_day) > 1:
        per_day.pop(min(per_day), None)
    avg = sum(per_day.values()) / len(per_day)
    return today_n, avg, round(today_n / avg, 1) if avg else None


def get(code: str, top: list[SearchRank] | None = None) -> Buzz:
    """한 종목의 커뮤니티 관심 지표. top을 넘기면 검색상위 목록을 재요청하지 않는다."""
    buzz = Buzz(code=code)
    try:
        if top is None:
            top = top_searched()
        hit = next((s for s in top if s.code == code), None)
        if hit:
            buzz.search_rank, buzz.search_ratio = hit.rank, hit.ratio
    except Exception:
        pass
    try:
        buzz.board_today, buzz.board_daily_avg, buzz.board_ratio = board_buzz(code)
    except Exception:
        pass

    parts = []
    if buzz.search_rank:
        parts.append(f"네이버 검색상위 {buzz.search_rank}위 (검색비율 {buzz.search_ratio})")
    if buzz.board_ratio is None and buzz.board_today:
        parts.append(f"토론실 오늘 {buzz.board_today}건+ — 평소 대비 급증(측정 상한 초과)")
    elif buzz.board_today or buzz.board_daily_avg:
        parts.append(f"토론실 오늘 {buzz.board_today}건, 평소 하루 {buzz.board_daily_avg:.0f}건"
                     + (f" (×{buzz.board_ratio})" if buzz.board_ratio else ""))
    buzz.note = " · ".join(parts) if parts else "커뮤니티 관심 데이터 없음"
    return buzz

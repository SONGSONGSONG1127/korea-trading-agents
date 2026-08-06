# -*- coding: utf-8 -*-
"""
Agent 1: 뉴스/공시 수집 에이전트 (v2)

네이버 증권에서 종목 뉴스와 공시를 수집하고:
- 강도 구분 가중 사전으로 제목 감성을 실수 점수(-1 ~ +1)로 계산
- 공시는 유형별 이벤트 규칙(유상증자/자사주/감자 등)으로 별도 스코어링
- 유사 제목(Jaccard ≥ 0.6)은 같은 사건으로 병합해 중복 부풀림 방지
- 시간 감쇠(반감기 3일)로 최신 기사에 가중
- 최근 5일 내 강한 악재(≤ -0.7)는 베토 플래그로 노출
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
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

# 강도 구분 가중 사전 (강한 단어 ±0.7~1.0, 약한 단어 ±0.3)
WEIGHTED_WORDS = {
    # 강한 긍정
    "급등": 0.7, "신고가": 0.7, "상한가": 0.9, "흑자전환": 0.7, "사상 최대": 0.7,
    "역대 최대": 0.7, "어닝서프라이즈": 0.7, "대규모 수주": 0.7,
    # 약한 긍정
    "상승": 0.3, "강세": 0.3, "호재": 0.3, "수주": 0.3, "흑자": 0.3, "돌파": 0.3,
    "성장": 0.3, "개선": 0.3, "호실적": 0.4, "증가": 0.3, "확대": 0.3, "배당": 0.3,
    "자사주": 0.4, "상향": 0.4, "기대": 0.2, "회복": 0.3, "반등": 0.3, "계약": 0.3,
    "출시": 0.2, "진출": 0.2, "호조": 0.3, "최고": 0.3,
    # 강한 부정
    "급락": -0.7, "신저가": -0.7, "하한가": -0.9, "적자전환": -0.7, "횡령": -0.9,
    "배임": -0.9, "상장폐지": -1.0, "거래정지": -0.9, "파산": -0.9, "분식": -0.9,
    # 약한 부정
    "하락": -0.3, "약세": -0.3, "악재": -0.3, "적자": -0.4, "부진": -0.3, "감소": -0.3,
    "축소": -0.3, "소송": -0.4, "제재": -0.4, "리스크": -0.3, "우려": -0.3, "하향": -0.4,
    "손실": -0.4, "충격": -0.4, "불확실": -0.3, "유상증자": -0.6, "감자": -0.7,
    "조사": -0.4, "위기": -0.4, "경고": -0.3, "철수": -0.3, "연기": -0.3,
}

# 부정어 뒤집기 패턴 ("우려 완화"류)
REVERSAL_PATTERNS = [
    (re.compile(r"우려\s*(완화|해소|불식)"), 0.4),
    (re.compile(r"불확실(성)?\s*(완화|해소)"), 0.4),
    (re.compile(r"리스크\s*(완화|해소)"), 0.4),
]

# 공시 유형별 이벤트 규칙 (제목이 정형화되어 있어 룰 기반이 잘 작동)
DISCLOSURE_RULES = [
    (re.compile(r"유상증자"), -0.6),
    (re.compile(r"무상증자"), 0.4),
    (re.compile(r"자기주식.*취득|자사주.*(매입|취득)"), 0.5),
    (re.compile(r"자기주식.*처분"), -0.3),
    (re.compile(r"전환사채|신주인수권부"), -0.5),
    (re.compile(r"단일판매.*공급계약|공급계약\s*체결"), 0.4),
    (re.compile(r"감자"), -0.7),
    (re.compile(r"현금.*배당|배당.*결정"), 0.3),
    (re.compile(r"관리종목|투자주의환기|불성실공시"), -0.8),
    (re.compile(r"횡령|배임"), -0.9),
    (re.compile(r"매매거래정지"), -1.0),
    (re.compile(r"액면분할"), 0.2),
]

DECAY_HALF_LIFE_DAYS = 3.0
EVENT_ABS_THRESHOLD = 0.6   # 근거에 항상 노출할 강한 이벤트
VETO_THRESHOLD = -0.7       # 최근 5일 내 이 이하 악재 → 매수 의견 베토


@dataclass
class NewsItem:
    title: str
    source: str
    date: str
    url: str
    kind: str = "뉴스"          # 뉴스 | 공시
    sentiment: float = 0.0      # -1.0 ~ +1.0
    age_days: float = 0.0
    dup_count: int = 1          # 같은 사건으로 병합된 기사 수
    matched: str = ""           # 매칭된 키워드/공시 규칙 (근거 인용용)


@dataclass
class NewsReport:
    stock_name: str
    code: str
    items: list[NewsItem] = field(default_factory=list)   # 클러스터 대표 기사들
    raw_count: int = 0
    score: float = 0.0          # 시간 감쇠 가중 평균 (-1 ~ +1)
    events: list[NewsItem] = field(default_factory=list)  # 최근 5일 내 강한 이벤트
    veto: bool = False          # 강한 악재 베토
    summary: str = ""
    error: str | None = None


# ── 수집 ────────────────────────────────────────────────────────────────

def _get(url: str) -> BeautifulSoup:
    res = requests.get(url, headers=HEADERS, timeout=10)
    res.raise_for_status()
    # 네이버 증권은 페이지마다 UTF-8/EUC-KR이 섞여 있어 헤더에 charset이 없으면 EUC-KR로 처리
    if "charset" not in (res.headers.get("Content-Type") or "").lower():
        res.encoding = "euc-kr"
    return BeautifulSoup(res.text, "lxml")


def fetch_stock_name(code: str) -> str:
    try:
        soup = _get(f"https://finance.naver.com/item/main.naver?code={code}")
        tag = soup.select_one("div.wrap_company h2 a")
        if tag:
            return tag.get_text(strip=True)
    except Exception:
        pass
    return code


def _parse_age_days(date_str: str) -> float:
    for fmt in ("%Y.%m.%d %H:%M", "%Y.%m.%d"):
        try:
            dt = datetime.strptime(date_str.strip(), fmt)
            return max(0.0, (datetime.now() - dt).total_seconds() / 86400)
        except ValueError:
            continue
    return 3.0  # 파싱 실패 시 중간값


def _fetch_rows(url: str, kind: str, source_default: str) -> list[NewsItem]:
    items: list[NewsItem] = []
    soup = _get(url)
    for row in soup.select("table.type5 tr, table.type6 tr"):
        title_td = row.select_one("td.title a") or row.select_one("td a")
        info_td = row.select_one("td.info")
        date_td = row.select_one("td.date")
        if not (title_td and date_td):
            continue
        title = title_td.get_text(strip=True)
        if not title:
            continue
        href = title_td.get("href", "")
        if href.startswith("/"):
            href = "https://finance.naver.com" + href
        date_str = date_td.get_text(strip=True)
        items.append(
            NewsItem(
                title=title,
                source=info_td.get_text(strip=True) if info_td else source_default,
                date=date_str,
                url=href,
                kind=kind,
                age_days=_parse_age_days(date_str),
            )
        )
    return items


# ── 감성 스코어링 ────────────────────────────────────────────────────────

def score_item(item: NewsItem) -> None:
    """제목을 스코어링해 sentiment/matched를 채운다."""
    title = item.title
    # 1) 공시 이벤트 규칙 우선 (공시이거나 제목이 규칙에 매칭되면)
    disclosure_hits = [(rx.pattern, w) for rx, w in DISCLOSURE_RULES if rx.search(title)]
    strongest = max((w for _, w in disclosure_hits), key=abs, default=0.0)
    if disclosure_hits and (item.kind == "공시" or abs(strongest) >= 0.5):
        pattern, weight = max(disclosure_hits, key=lambda x: abs(x[1]))
        item.sentiment = weight
        item.matched = f"공시 규칙 '{pattern}'"
        return

    # 2) 가중 키워드 사전
    score = 0.0
    hits = []
    for word, w in WEIGHTED_WORDS.items():
        if word in title:
            score += w
            hits.append(word)
    # 3) 뒤집기 패턴 ("우려 완화" 등)
    for rx, bonus in REVERSAL_PATTERNS:
        if rx.search(title):
            score += bonus + 0.3  # 부정어로 깎인 것 복원 + 긍정 가산
            hits.append(rx.pattern)
    item.sentiment = round(max(-1.0, min(1.0, score)), 2)
    item.matched = ", ".join(hits[:4])


def _jaccard(a: str, b: str) -> float:
    A, B = set(a.split()), set(b.split())
    return len(A & B) / (len(A | B) + 1e-9)


def cluster_items(items: list[NewsItem], threshold: float = 0.6) -> list[NewsItem]:
    """유사 제목을 같은 사건으로 병합 — 받아쓰기 기사로 인한 점수 부풀림 방지.
    대표는 |감성|이 가장 큰 기사."""
    reps: list[NewsItem] = []
    for it in items:
        merged = False
        for rep in reps:
            if _jaccard(it.title, rep.title) >= threshold:
                rep.dup_count += 1
                if abs(it.sentiment) > abs(rep.sentiment):
                    it.dup_count = rep.dup_count
                    reps[reps.index(rep)] = it
                merged = True
                break
        if not merged:
            reps.append(it)
    return reps


# ── 메인 ────────────────────────────────────────────────────────────────

def run(code: str, news_pages: int = 3) -> NewsReport:
    code = re.sub(r"\D", "", code).zfill(6)
    report = NewsReport(stock_name=fetch_stock_name(code), code=code)

    raw: list[NewsItem] = []
    try:
        for page in range(1, news_pages + 1):
            raw += _fetch_rows(
                f"https://finance.naver.com/item/news_news.naver?code={code}&page={page}&clusterId=",
                "뉴스", "",
            )
    except Exception as e:
        report.error = f"뉴스 수집 실패: {e}"
        return report
    try:
        raw += _fetch_rows(
            f"https://finance.naver.com/item/news_notice.naver?code={code}&page=1",
            "공시", "전자공시",
        )
    except Exception:
        pass  # 공시 탭 실패는 치명적이지 않음

    for it in raw:
        score_item(it)
    report.raw_count = len(raw)
    report.items = cluster_items(raw)

    # 시간 감쇠 가중 평균 (반감기 3일)
    if report.items:
        num = den = 0.0
        for it in report.items:
            w = 0.5 ** (it.age_days / DECAY_HALF_LIFE_DAYS)
            num += it.sentiment * w
            den += w
        report.score = round(max(-1.0, min(1.0, num / den)), 3) if den else 0.0

    # 강한 이벤트(최근 5일, |점수| ≥ 0.6)는 평균에 묻히지 않게 별도 노출 + 베토
    report.events = sorted(
        [it for it in report.items if it.age_days <= 5 and abs(it.sentiment) >= EVENT_ABS_THRESHOLD],
        key=lambda x: x.sentiment,
    )
    # 베토는 '공시'에만 발동 — 뉴스 제목의 급락 표현 등은 이미 기술 점수/레짐이 반영한다
    report.veto = any(it.sentiment <= VETO_THRESHOLD and it.kind == "공시" for it in report.events)

    pos = sum(1 for it in report.items if it.sentiment >= 0.2)
    neg = sum(1 for it in report.items if it.sentiment <= -0.2)
    neu = len(report.items) - pos - neg
    mood = "우호적" if report.score > 0.1 else "비우호적" if report.score < -0.1 else "중립적"
    report.summary = (
        f"기사/공시 {report.raw_count}건 → 중복 병합 후 {len(report.items)}개 사건. "
        f"긍정 {pos} / 부정 {neg} / 중립 {neu}, 시간 감쇠 반영 감성 {report.score:+.2f} ({mood})."
        + (f" ⚠️ 최근 5일 내 강한 이벤트 {len(report.events)}건." if report.events else "")
        + (" 🚫 강한 악재로 매수 의견 베토." if report.veto else "")
    )
    return report

# -*- coding: utf-8 -*-
"""
오늘의 운세·사주 — 만세력 기반 (재미로 보는 콘텐츠)

사주팔자: korean_lunar_calendar의 일진(간지) + 절기 기준 월주 + 야자시(23시~) 규칙.
오늘의 운세: 내 일간(日干) 오행 vs 오늘 일진 오행의 상생상극(비겁/식상/재성/관성/인성)
+ 지지 삼합·육합·충으로 점수·조언을 산출. 같은 날엔 항상 같은 결과(결정적 시드).

개인정보(이름·생년월일시)는 공개 레포에 올리지 않도록 secrets에서만 읽는다:
  SAJU_NAME, SAJU_BIRTH("YYYY-MM-DD HH:MM"), SAJU_GENDER — 미설정 시 조용히 비활성.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta

from .notify import _cred

# ── 간지 기초 데이터 ──────────────────────────────────────────────────────
_STEM_ELEM = {"갑": "목", "을": "목", "병": "화", "정": "화", "무": "토",
              "기": "토", "경": "금", "신": "금", "임": "수", "계": "수"}
_BRANCH_ELEM = {"자": "수", "축": "토", "인": "목", "묘": "목", "진": "토", "사": "화",
                "오": "화", "미": "토", "신": "금", "유": "금", "술": "토", "해": "수"}
_BRANCH_ANIMAL = {"자": "쥐", "축": "소", "인": "호랑이", "묘": "토끼", "진": "용", "사": "뱀",
                  "오": "말", "미": "양", "신": "원숭이", "유": "닭", "술": "개", "해": "돼지"}
_STEMS = "갑을병정무기경신임계"
_BRANCHES = "자축인묘진사오미신유술해"
_GEN = {"목": "화", "화": "토", "토": "금", "금": "수", "수": "목"}   # 상생
_CTL = {"목": "토", "토": "수", "수": "화", "화": "금", "금": "목"}   # 상극

# 삼합(세 글자 한 팀)·육합·충
_SAMHAP = [set("신자진"), set("사유축"), set("인오술"), set("해묘미")]
_YUKHAP = {"자": "축", "축": "자", "인": "해", "해": "인", "묘": "술", "술": "묘",
           "진": "유", "유": "진", "사": "신", "신": "사", "오": "미", "미": "오"}
_CHUNG = {"자": "오", "오": "자", "축": "미", "미": "축", "인": "신", "신": "인",
          "묘": "유", "유": "묘", "진": "술", "술": "진", "사": "해", "해": "사"}

_LUCK = {  # 오행별 행운 요소
    "목": ("청색", "3, 8", "동쪽"), "화": ("적색", "2, 7", "남쪽"),
    "토": ("황색", "5, 10", "중앙"), "금": ("흰색", "4, 9", "서쪽"),
    "수": ("검정·남색", "1, 6", "북쪽"),
}

# 십신(오행 단순화) 관계별 운세 텍스트
_REL = {
    "재성": {
        "title": "재성(財星)의 날 — 재물이 움직이는 날",
        "base": (82, 88),
        "총운": ["내가 다루고 통제할 수 있는 기운이 들어오는 날. 계획했던 일이 손에 잡힙니다.",
                 "노력의 대가가 눈에 보이는 날. 미뤄둔 결정을 실행에 옮기기 좋습니다.",
                 "재물의 흐름이 나를 향하는 날. 다만 욕심을 부리면 흐름이 끊깁니다."],
        "투자": ["관심 종목을 점검하고 분할로 접근해보기 좋은 기운입니다. 단, 몰빵은 재성을 깨뜨립니다.",
                 "수익 실현이든 신규 진입이든 '계획대로'가 핵심인 날. 시스템의 신호를 믿어보세요.",
                 "작은 이익을 확정하는 것이 큰 이익의 씨앗이 되는 날입니다."],
    },
    "인성": {
        "title": "인성(印星)의 날 — 귀인과 배움의 날",
        "base": (72, 78),
        "총운": ["나를 도와주는 기운이 들어오는 날. 조언을 구하면 뜻밖의 답을 얻습니다.",
                 "공부와 분석이 잘 되는 날. 서두르기보다 준비에 힘쓰면 이득입니다.",
                 "문서·계약·정보에 운이 따르는 날. 리포트를 꼼꼼히 읽어보세요."],
        "투자": ["매매보다 리서치가 돈 버는 날. 관심 종목의 재무제표를 뜯어보기 좋습니다.",
                 "브리핑과 트랙레코드를 복기해보세요. 과거의 교훈이 오늘의 수익이 됩니다.",
                 "신규 진입보다 보유 종목의 논리를 재점검하기 좋은 기운입니다."],
    },
    "식상": {
        "title": "식상(食傷)의 날 — 아이디어와 실행의 날",
        "base": (68, 74),
        "총운": ["내 기운이 밖으로 뻗는 날. 표현하고 움직일수록 길이 열립니다.",
                 "아이디어가 샘솟는 날. 다만 말이 앞서면 실속이 샙니다.",
                 "활동량이 운을 부르는 날. 몸을 움직이고 사람을 만나보세요."],
        "투자": ["새로운 섹터·종목을 발굴하기 좋은 날. 스크리너를 돌려보세요.",
                 "감이 좋아도 검증은 필수. 아이디어는 백데이터로 확인한 뒤 움직이세요.",
                 "포트폴리오에 새 바람을 넣고 싶다면 소액으로 시험해보기 좋은 기운입니다."],
    },
    "비겁": {
        "title": "비겁(比劫)의 날 — 경쟁과 고집의 날",
        "base": (55, 62),
        "총운": ["나와 같은 기운이 겹치는 날. 추진력은 좋지만 고집이 화를 부를 수 있습니다.",
                 "경쟁자가 많아지는 날. 남 따라가기보다 내 페이스를 지키는 게 상책입니다.",
                 "동료·지인과의 관계에서 득실이 갈리는 날. 돈 거래는 피하세요."],
        "투자": ["뇌동매매 주의보. 남들이 산다고 따라 사면 비겁운이 지갑을 노립니다.",
                 "오늘은 원칙 매매의 날. 손절선과 비중 규칙을 그대로 지키는 것이 최선입니다.",
                 "수익률 비교는 금물. 내 계좌의 리듬에만 집중하세요."],
    },
    "관성": {
        "title": "관성(官星)의 날 — 절제와 인내의 날",
        "base": (48, 56),
        "총운": ["나를 누르는 기운이 들어오는 날. 규율을 지키면 오히려 평온합니다.",
                 "압박과 책임이 느껴지는 날. 무리한 확장보다 정리정돈이 길합니다.",
                 "윗사람·규칙과 엮이는 날. 겸손하게 한 발 물러서면 손해가 없습니다."],
        "투자": ["공격보다 수비의 날. 현금 비중이 오늘의 효자 노릇을 합니다.",
                 "변동성에 휘둘리기 쉬운 기운. 신규 진입은 하루 미뤄도 늦지 않습니다.",
                 "손절 신호가 왔다면 미련 없이 따르세요. 관성의 날엔 규칙이 곧 방패입니다."],
    },
}


# ── 사주 계산 ─────────────────────────────────────────────────────────────

def _day_gapja(d: datetime) -> str:
    from korean_lunar_calendar import KoreanLunarCalendar
    c = KoreanLunarCalendar()
    c.setSolarDate(d.year, d.month, d.day)
    return c.getGapJaString().split()[2][:2]   # "경인일" → "경인"


def _year_gapja(d: datetime) -> str:
    from korean_lunar_calendar import KoreanLunarCalendar
    c = KoreanLunarCalendar()
    c.setSolarDate(d.year, d.month, d.day)
    return c.getGapJaString().split()[0][:2]


# 절기 기준 월지 — 달력 순서(1월 소한 → 12월 대설)의 대략적 절입일.
# 월 중순 출생이면 오차 없음 (절입일 경계 ±1일 출생만 수동 확인 필요)
_TERM_BRANCH = [(1, 6, "축"), (2, 4, "인"), (3, 6, "묘"), (4, 5, "진"), (5, 6, "사"),
                (6, 6, "오"), (7, 7, "미"), (8, 8, "신"), (9, 8, "유"), (10, 8, "술"),
                (11, 7, "해"), (12, 7, "자")]


def _month_pillar(d: datetime, year_stem: str) -> str:
    branch = "자"   # 1/1~소한(1/5)은 전년 대설 이후 자월
    for m, day, br in _TERM_BRANCH:
        if (d.month, d.day) >= (m, day):
            branch = br
    # 월간: 연간에 따른 인월 시작 천간 (갑기→병, 을경→무, 병신→경, 정임→임, 무계→갑)
    start = {"갑": "병", "기": "병", "을": "무", "경": "무", "병": "경",
             "신": "경", "정": "임", "임": "임", "무": "갑", "계": "갑"}[year_stem]
    steps = (_BRANCHES.index(branch) - _BRANCHES.index("인")) % 12
    stem = _STEMS[(_STEMS.index(start) + steps) % 10]
    return stem + branch


def _hour_pillar(day_stem: str, hour: int) -> str:
    branch = _BRANCHES[((hour + 1) // 2) % 12]
    start = {"갑": "갑", "기": "갑", "을": "병", "경": "병", "병": "무",
             "신": "무", "정": "경", "임": "경", "무": "임", "계": "임"}[day_stem]
    stem = _STEMS[(_STEMS.index(start) + _BRANCHES.index(branch)) % 10]
    return stem + branch


def _pillars(birth: datetime) -> dict:
    """연·월·일·시주. 23시 이후 출생은 야자시 — 일주를 다음 날로 넘긴다."""
    day_base = birth + timedelta(days=1) if birth.hour >= 23 else birth
    year = _year_gapja(birth)
    day = _day_gapja(day_base)
    return {
        "year": year, "month": _month_pillar(birth, year[0]),
        "day": day, "hour": _hour_pillar(day[0], birth.hour),
    }


def _relation(me: str, other: str) -> str:
    e1, e2 = _STEM_ELEM[me], _STEM_ELEM[other]
    if e1 == e2:
        return "비겁"
    if _GEN[e1] == e2:
        return "식상"
    if _CTL[e1] == e2:
        return "재성"
    if _CTL[e2] == e1:
        return "관성"
    return "인성"


# ── 오늘의 운세 ───────────────────────────────────────────────────────────

def today() -> dict | None:
    """운세 dict 반환. secrets(SAJU_*) 미설정 시 None."""
    name = _cred("SAJU_NAME")
    birth_s = _cred("SAJU_BIRTH")
    if not name or not birth_s:
        return None
    try:
        birth = datetime.strptime(birth_s.strip(), "%Y-%m-%d %H:%M")
    except ValueError:
        return None

    now = datetime.now()
    p = _pillars(birth)
    today_gj = _day_gapja(now)
    me_stem, me_branch = p["day"][0], p["year"][1]   # 일간(나), 연지(띠)
    t_stem, t_branch = today_gj[0], today_gj[1]

    rel = _relation(me_stem, t_stem)
    rd = _REL[rel]
    rng = random.Random(f"{now.date()}|{name}|{birth_s}")

    lo, hi = rd["base"]
    total = max(5, min(99, rng.randint(lo, hi) + rng.randint(-6, 6)))
    wealth = max(5, min(99, total + (8 if rel == "재성" else 0) + rng.randint(-8, 8)))

    # 지지 궁합: 내 띠(연지) vs 오늘 일지 — 삼합/육합은 가산, 충은 감산
    bonus, bonus_txt = 0, ""
    if any(me_branch in s and t_branch in s and me_branch != t_branch for s in _SAMHAP):
        bonus, bonus_txt = 7, f"오늘 일지({t_branch})와 내 띠({me_branch})가 삼합 — 흐름이 나를 돕습니다"
    elif _YUKHAP.get(me_branch) == t_branch:
        bonus, bonus_txt = 5, f"오늘 일지({t_branch})와 내 띠({me_branch})가 육합 — 귀인이 가깝습니다"
    elif _CHUNG.get(me_branch) == t_branch:
        bonus, bonus_txt = -8, f"오늘 일지({t_branch})와 내 띠({me_branch})가 충(沖) — 돌발 변수 주의"
    invest = max(5, min(99, (total + wealth) // 2 + bonus + rng.randint(-5, 5)))

    t_elem = _STEM_ELEM[t_stem]
    color, number, direction = _LUCK[_GEN[t_elem]]   # 오늘 기운이 생(生)하는 오행 = 행운 요소

    return {
        "name": name,
        "date": now.strftime("%m/%d (%a)"),
        "saju": f"{p['year']}년 {p['month']}월 {p['day']}일 {p['hour']}시",
        "me": f"{me_stem}{_STEM_ELEM[me_stem]}(日干)",
        "zodiac": f"{_BRANCH_ANIMAL[me_branch]}띠",
        "today_ganji": f"{today_gj}일 ({_BRANCH_ANIMAL[t_branch]}의 날)",
        "relation": rel,
        "relation_title": rd["title"],
        "total": total, "wealth": wealth, "invest": invest,
        "summary": rng.choice(rd["총운"]),
        "invest_advice": rng.choice(rd["투자"]),
        "bonus_txt": bonus_txt,
        "lucky": f"행운의 색 {color} · 숫자 {number} · 방위 {direction}",
    }


def telegram_section() -> str:
    f = today()
    if not f:
        return ""
    bar = lambda v: "●" * round(v / 20) + "○" * (5 - round(v / 20))
    lines = [
        f"*🔮 오늘의 운세* — {f['name']} ({f['zodiac']} · {f['me']})",
        f"  오늘은 {f['today_ganji']} · {f['relation_title']}",
        f"  총운 {bar(f['total'])} {f['total']} · 재물 {bar(f['wealth'])} {f['wealth']} · 투자 {bar(f['invest'])} {f['invest']}",
        f"  {f['summary']}",
        f"  💰 {f['invest_advice']}",
    ]
    if f["bonus_txt"]:
        lines.append(f"  ✨ {f['bonus_txt']}")
    lines.append(f"  🍀 {f['lucky']} · (재미로 보세요)")
    return "\n".join(lines)

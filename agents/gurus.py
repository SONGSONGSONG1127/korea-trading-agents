# -*- coding: utf-8 -*-
"""
투자 귀재들의 한줄평 (가상) + 심층리서치 프롬프트 생성.

각 인물의 공개된 투자 철학(가치/추세/역발상/분산/사이클)을 흉내 낸 규칙 기반
가상 코멘트다 — 실제 발언·자문이 아니며 재미와 관점 환기용. 같은 날 같은 종목이면
항상 같은 코멘트(결정적 시드).

ctx 키:
  mode: "hold"(보유 점검) | "scout"(신규 분석)
  name, code, date
  ret_pct(보유 수익률), below_stop, near_stop, rebound(본전까지 %), acct_pct(계좌 영향 %p)
  score(기술 -100~100), combined(합산 0~100), fund_grade, per, sector_per
  regime_trend("순풍"/"중립"/"역풍"), regime_vol("저"/"중"/"고"), veto(악재 여부)
"""
from __future__ import annotations

import random
from datetime import date as _date


def _g(ctx, k, d=None):
    return ctx.get(k, d)


def _buffett(c, r):
    if _g(c, "mode") == "hold":
        if (_g(c, "ret_pct") or 0) < -10:
            pool = ["주가는 당신에게 아무것도 말해주지 않습니다. 처음 이 기업을 산 이유가 아직 살아있습니까?",
                    "10년 들고 갈 자신이 없는 기업이라면, 10분도 들고 있지 마십시오.",
                    "훌륭한 기업이 세일 중인 건지, 평범한 기업을 비싸게 산 건지 — 답은 재무제표에 있습니다."]
        else:
            pool = ["시장의 소음보다 기업의 이익을 보십시오. 그게 전부입니다.",
                    "자주 쳐다볼수록 자주 틀립니다. 기업이 멀쩡하면 계좌도 멀쩡해집니다."]
    else:
        gd = str(_g(c, "fund_grade") or "")
        if gd in ("매우 우량", "우량"):
            pool = ["적당한 기업을 싸게 사는 것보다 훌륭한 기업을 적당한 값에 사는 게 낫습니다. 재무는 합격점이군요.",
                    "해자가 있는지 보십시오. 숫자가 좋아도 경쟁자가 쉽게 베낄 수 있다면 오래 못 갑니다."]
        else:
            pool = ["이해할 수 없는 사업과 약한 재무 — 제 원 안에는 들어오지 않는 공입니다.",
                    "싸 보이는 데는 이유가 있는 법입니다. 그 이유부터 확인하십시오."]
    return r.choice(pool)


def _lynch(c, r):
    if _g(c, "mode") == "hold" and (_g(c, "ret_pct") or 0) < 0:
        pool = ["주가가 떨어진 것과 스토리가 깨진 것은 다릅니다. 산 이유(스토리)를 다시 확인하세요.",
                "최악의 실수는 주가만 보고 파는 것, 두 번째 실수는 스토리가 깨졌는데 안 파는 것입니다.",
                "이 회사가 뭘로 돈 버는지 2분 안에 설명 못 하면, 들고 있을 이유도 설명 못 합니다."]
    else:
        pool = ["당신이 아는 것에 투자하세요. 이 회사 제품을 설명할 수 있습니까?",
                "차트가 아니라 실적이 주가를 만듭니다. 다음 분기 이익이 어디서 나올지 보세요.",
                "10루타는 인내에서 나옵니다. 다만 그 인내는 '성장하는 기업'에만 허락됩니다."]
    return r.choice(pool)


def _livermore(c, r):
    if _g(c, "below_stop"):
        pool = ["손실을 키우는 것이 투기꾼의 유일한 죄악입니다. 선을 깼으면 시장이 이미 답을 준 겁니다.",
                "희망은 전략이 아닙니다. 추세가 꺾였으면 물러섰다가 다시 들어오면 됩니다.",
                "제가 돈을 번 건 판단이 아니라 엉덩이 덕분이지만 — 그건 추세가 내 편일 때 이야기입니다."]
    elif _g(c, "near_stop"):
        pool = ["선 근처에서 고민하지 마십시오. 깨지면 행동하고, 안 깨지면 잊으십시오. 미리 정한 대로.",
                "시장과 논쟁하지 마세요. 내일 가격이 당신 대신 결론을 내줄 겁니다."]
    elif (_g(c, "score") or 0) >= 30:
        pool = ["추세가 당신 편입니다. 이익은 달리게 두고, 손절선만 끌어올리십시오.",
                "맞을 때 크게 먹는 것이 이 게임의 전부입니다. 지금은 세는 날이 아니라 달리는 날."]
    else:
        pool = ["확신 없는 자리에선 쉬는 것도 포지션입니다. 시장은 내일도 열립니다.",
                "돈은 매매가 아니라 기다림에서 벌립니다."]
    return r.choice(pool)


def _kostolany(c, r):
    if str(_g(c, "regime_vol")) == "고" or _g(c, "mode") == "hold" and (_g(c, "ret_pct") or 0) < -10:
        pool = ["시장이 공포에 떨 때 중요한 건 주식이 아니라 현금과 배짱입니다. 둘 다 있으면 기회, 없으면 인내.",
                "부화뇌동파는 늘 같은 자리에서 돈을 잃습니다. 당신은 소신파입니까, 부화뇌동파입니까?",
                "폭락은 소신파가 부화뇌동파의 주식을 싸게 넘겨받는 행사입니다. 문제는 당신이 어느 쪽이냐는 것."]
    else:
        pool = ["주가와 경제는 산책하는 주인과 개입니다. 개가 앞서 달려도 결국 주인에게 돌아옵니다.",
                "수면제를 먹고 10년 뒤에 보라 했지만 — 그 전에 '좋은 종목'인지부터 확인하는 게 순서입니다."]
    return r.choice(pool)


def _dalio(c, r):
    ap = _g(c, "acct_pct")
    if ap is not None and ap <= -3:
        pool = ["한 종목이 계좌 전체를 이만큼 흔든다면, 종목이 아니라 포트폴리오 설계가 문제입니다.",
                "고통 + 성찰 = 진보. 이 손실에서 규칙 하나를 얻어내면 수업료 값은 한 겁니다."]
    elif _g(c, "mode") == "hold":
        pool = ["이 포지션의 손실이 계좌에서 차지하는 비중을 보니, 시스템은 아직 당신을 보호하고 있습니다.",
                "분산이 잘 된 계좌에선 어떤 한 종목의 운명도 치명적이지 않습니다. 그게 성배입니다."]
    else:
        pool = ["서로 다르게 움직이는 15개의 수익원 — 그게 투자의 성배입니다. 이 종목은 당신 계좌에 '다른 움직임'을 더합니까?",
                "당신이 맞을 확률보다, 틀렸을 때 얼마나 잃는지가 더 중요합니다. 비중부터 정하십시오."]
    return r.choice(pool)


def _marks(c, r):
    if str(_g(c, "regime_trend")) == "역풍" or str(_g(c, "regime_vol")) == "고":
        pool = ["'지금 사이클의 어디쯤인가'를 묻는 것이 예측보다 낫습니다. 모두가 비관할 때 위험은 이미 많이 빠져나갔습니다.",
                "가장 큰 위험은 가격이 아니라 '위험이 없다는 믿음'에서 옵니다. 공포가 있는 시장은 차라리 건강합니다.",
                "나무가 하늘까지 자라지 않듯, 대부분의 것은 결국 평균으로 돌아옵니다 — 공포도 마찬가지입니다."]
    else:
        pool = ["탁월한 성과는 남과 다르게, 그리고 맞게 행동할 때만 나옵니다. 남들과 같은 생각이면 수익도 평균입니다.",
                "싸게 사는 것이 유일하게 믿을 만한 수익의 원천입니다. 지금 가격에 무엇이 반영돼 있습니까?"]
    return r.choice(pool)


GURUS = [
    ("워런 버핏", "🏛️", "가치·장기", _buffett),
    ("피터 린치", "🔍", "스토리·실적", _lynch),
    ("제시 리버모어", "📉", "추세·손절", _livermore),
    ("앙드레 코스톨라니", "🎭", "역발상·인내", _kostolany),
    ("레이 달리오", "⚖️", "분산·리스크", _dalio),
    ("하워드 막스", "🌊", "사이클·심리", _marks),
]

DISCLAIMER = ("각 인물의 공개된 투자 철학을 흉내 낸 규칙 기반 가상 코멘트입니다. "
              "실제 발언·자문이 아니며, 서로 모순될 수 있는 '관점'을 보여주는 용도입니다.")


def comments(ctx: dict) -> list[dict]:
    """[{guru, icon, style, line}] — 같은 날 같은 종목이면 동일 결과."""
    out = []
    for name, icon, style, fn in GURUS:
        r = random.Random(f"{_date.today()}|{ctx.get('code', '')}|{name}")
        try:
            out.append({"guru": name, "icon": icon, "style": style, "line": fn(ctx, r)})
        except Exception:
            continue
    return out


# ── 심층리서치 프롬프트 ──────────────────────────────────────────────────

def research_prompt(ctx: dict) -> str:
    """Claude/ChatGPT 딥리서치에 붙여넣을 프롬프트 (오늘 데이터 자동 삽입)."""
    name, code = ctx.get("name", ""), ctx.get("code", "")
    kr = ctx.get("market", "KR") != "US"
    uni = "원" if kr else "달러"
    nat = "한국 상장기업" if kr else "미국 상장기업"

    snap = [f"- 기준일: {_date.today()}"]
    if ctx.get("price") is not None:
        snap.append(f"- 주가: {ctx['price']:,.0f}{uni}" if kr else f"- 주가: ${ctx['price']:,.2f}")
    if ctx.get("combined") is not None:
        snap.append(f"- 내 시스템 합산 점수: {ctx['combined']}/100 (기술 {ctx.get('score100', '?')} · 펀더멘탈 {ctx.get('fund_score', '?')})")
    if ctx.get("per") is not None:
        s = f"- PER {ctx['per']:.1f}배"
        if ctx.get("sector_per"):
            s += f" (업종 평균 {ctx['sector_per']:.1f}배)"
        snap.append(s)
    if ctx.get("pbr") is not None:
        snap.append(f"- PBR {ctx['pbr']:.2f}배")
    if ctx.get("roe") is not None:
        snap.append(f"- ROE {ctx['roe']:.1f}%")
    if ctx.get("regime_trend"):
        snap.append(f"- 시장 레짐: {ctx['regime_trend']} · 변동성 {ctx.get('regime_vol', '?')}")
    if ctx.get("news_summary"):
        snap.append(f"- 최근 뉴스 요약: {str(ctx['news_summary'])[:300]}")
    if ctx.get("ret_pct") is not None:
        snap.append(f"- 현재 보유 중: 수익률 {ctx['ret_pct']:+.1f}% (손절/보유 판단이 핵심 질문)")

    return f"""당신은 20년 경력의 바이사이드 애널리스트입니다. {nat} {name}({code})에 대한 심층 리서치를 수행해 주세요. 웹 검색으로 최신 정보를 확인하고, 모든 수치에 기준일과 출처를 명시하세요.

[현재 스냅샷 — 내 분석 시스템 기준]
{chr(10).join(snap)}

[요청 사항 — 각 항목마다 근거·출처·날짜 명시]
1. 사업 구조: 무엇으로 돈을 버는 회사인지 — 사업부별 매출·이익 비중, 핵심 제품/고객
2. 실적: 최근 4개 분기 실적 추이, 다음 분기·연간 컨센서스, 최근 가이던스/어닝 서프라이즈 여부
3. 산업·경쟁: 업황 사이클의 현재 위치, 주요 경쟁사 대비 포지션과 점유율 변화
4. 수급·주주: 최근 외국인/기관 매매 동향, 대주주·자사주·증자/전환사채 이슈{"" if not kr else ", 공매도 잔고"}
5. 리스크: 이 회사가 크게 망가질 수 있는 시나리오 3가지와 각각의 조기 경보 지표
6. 밸류에이션: 서로 다른 3가지 방법(상대가치·과거 밴드·절대가치)으로 적정가 범위 산출, 계산 과정 포함
7. 불 vs 베어: 가장 강한 매수 논리 3개 vs 가장 강한 매도 논리 3개 — 같은 무게로
8. 결론: 1년 관점 상승/기본/하락 시나리오별 목표가와 주관적 확률, 그리고 진입·보유·정리 판단 전 확인할 체크리스트 5개

[규칙]
- 모르면 모른다고 쓰고, 추정에는 '추정'을 명시할 것
- 최근 6개월 내 정보를 우선할 것 (오래된 정보는 날짜를 밝힐 것)
- 낙관과 비관을 같은 무게로 다루고, 내 시스템 점수와 다른 결론이 나오면 그 이유를 짚을 것
- 마지막에 전체 내용을 10줄 요약으로 정리할 것"""

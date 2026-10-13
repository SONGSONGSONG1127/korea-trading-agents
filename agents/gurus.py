# -*- coding: utf-8 -*-
"""
투자 귀재들의 한줄평 (가상) + 심층리서치 프롬프트 생성.

작동 방식 (투명 공개): 각 인물의 '공개된 투자 철학'을 조건문으로 코드화한
규칙 기반 엔진이다. 현재 종목 상태(손절선 이탈 여부·수익률·기술점수·레짐·계좌
비중)를 보고, 그 철학이라면 할 법한 말과 입장을 고른다. 실제 인물·AI·자문이
아니며, 일부러 서로 다른 결론이 나오게 설계돼 있다(한쪽 쏠림 방지).
같은 날 같은 종목이면 항상 같은 결과(결정적 시드).

입장(stance):
  보유 점검(hold) 모드 → "정리" / "관망" / "보유"
  신규 분석(scout) 모드 → "긍정" / "중립" / "부정"
"""
from __future__ import annotations

import random
from datetime import date as _date

STANCE_EMOJI = {"정리": "🔻", "관망": "⏸", "보유": "💎",
                "긍정": "👍", "중립": "⏸", "부정": "👎"}


def _g(ctx, k, d=None):
    return ctx.get(k, d)


def _buffett(c, r):
    if _g(c, "mode") == "hold":
        if (_g(c, "ret_pct") or 0) < -10:
            return "관망", r.choice([
                "팔지 말지는 주가가 아니라 기업이 답합니다. 처음 산 이유가 살아있으면 보유, 깨졌으면 정리 — 오늘 그걸 확인하세요.",
                "훌륭한 기업이 세일 중인 건지, 평범한 기업을 비싸게 산 건지부터 확인하십시오. 답은 재무제표에 있습니다."])
        return "보유", r.choice([
            "기업이 멀쩡하면 계좌도 결국 멀쩡해집니다. 시장의 소음보다 기업의 이익을 보십시오.",
            "자주 쳐다볼수록 자주 틀립니다. 지금은 들고 가는 쪽입니다."])
    gd = str(_g(c, "fund_grade") or "")
    if gd in ("매우 우량", "우량"):
        return "긍정", r.choice([
            "재무는 합격점입니다. 남은 질문은 하나 — 경쟁자가 쉽게 못 베끼는 해자가 있습니까?",
            "훌륭한 기업을 적당한 값에 — 지금 가격이 '적당한 값'인지 업종 평균과 비교해보세요."])
    if gd in ("취약", "위험"):
        return "부정", r.choice([
            "싸 보이는 데는 이유가 있는 법입니다. 재무가 약한 기업은 제 원 밖의 공입니다.",
            "이해 못 할 사업 + 약한 재무 — 저라면 치지 않는 공입니다."])
    return "중립", "제 기준(우량한 재무·이해 가능한 사업)으로 판단할 정보가 아직 부족합니다. 재무제표부터."


def _lynch(c, r):
    if _g(c, "mode") == "hold":
        if (_g(c, "ret_pct") or 0) < 0:
            return "관망", r.choice([
                "주가 하락과 스토리 훼손은 다릅니다. 산 이유(스토리)가 그대로면 보유, 깨졌으면 정리 — 스토리부터 다시 쓰세요.",
                "최악은 주가만 보고 파는 것, 그다음은 스토리가 깨졌는데 안 파는 것. 오늘 할 일은 스토리 점검입니다."])
        return "보유", "실적이 주가를 만듭니다. 스토리가 살아있으면 10루타는 인내에서 나옵니다."
    if (_g(c, "combined") or 50) >= 55:
        return "긍정", "숫자는 나쁘지 않네요. 이 회사가 뭘로 돈 버는지 2분 안에 설명할 수 있으면 사도 됩니다."
    return "중립", r.choice([
        "차트가 아니라 실적이 주가를 만듭니다. 다음 분기 이익이 어디서 나올지 설명해보세요.",
        "이 회사 제품을 설명할 수 있습니까? 설명 못 하면 당신 종목이 아닙니다."])


def _livermore(c, r):
    if _g(c, "below_stop"):
        return "정리", r.choice([
            "손실을 키우는 것이 유일한 죄악입니다. 선을 깼으면 시장이 이미 답을 줬습니다 — 물러났다가 다시 들어오면 됩니다.",
            "희망은 전략이 아닙니다. 제 규칙이라면 여기선 정리입니다."])
    if _g(c, "near_stop"):
        return "관망", "선 근처에서 고민하지 마십시오. 깨지면 팔고, 안 깨지면 잊는다 — 미리 정한 대로만."
    sc = _g(c, "score") or 0
    if _g(c, "mode") == "hold":
        if sc >= 30:
            return "보유", "추세가 당신 편입니다. 이익은 달리게 두고 손절선만 끌어올리십시오."
        return "관망", "추세도 신호도 애매할 땐 쉬는 것도 포지션입니다. 선이 지켜지는지만 보세요."
    if sc >= 30:
        return "긍정", "추세가 살아있는 종목입니다. 단, 들어가기 전에 나올 선부터 정하십시오."
    if sc <= -10:
        return "부정", "꺾인 추세에 맞서지 마십시오. 바닥 잡기는 제 방식이 아닙니다."
    return "중립", "돈은 매매가 아니라 기다림에서 벌립니다. 확신 없는 자리는 패스."


def _kostolany(c, r):
    fear = str(_g(c, "regime_vol")) == "고" or str(_g(c, "regime_trend")) == "역풍"
    if _g(c, "mode") == "hold":
        if fear or (_g(c, "ret_pct") or 0) < -10:
            return "보유", r.choice([
                "폭락장의 투매는 부화뇌동파가 소신파에게 주식을 싸게 넘기는 행사입니다. 급할 것 없으면 넘기는 쪽에 서지 마세요.",
                "공포에 파는 것이 가장 비싼 수업료입니다. 여유 자금이라면 버티는 쪽, 빚이라면 이야기가 다릅니다."])
        return "보유", "주가와 기업은 주인과 산책하는 개입니다. 개가 멀리 가도 결국 주인에게 돌아옵니다."
    if fear:
        return "긍정", "모두가 떨 때가 소신파의 장보기 시간입니다. 단, 전제는 '좋은 기업'과 '여유 현금'입니다."
    return "중립", "지금은 싸지도 비싸지도 않은 구간 — 서두를 이유가 없습니다."


def _dalio(c, r):
    ap = _g(c, "acct_pct")
    if _g(c, "mode") == "hold":
        if ap is not None and ap <= -3:
            return "정리", "한 종목이 계좌를 이만큼 흔들면 종목이 아니라 설계의 문제입니다. 비중부터 줄이십시오 (전량일 필요는 없습니다)."
        return "보유", "이 종목 손실이 계좌에서 차지하는 비중을 보면, 시스템이 아직 당신을 보호하고 있습니다. 설계대로 가십시오."
    return "중립", r.choice([
        "맞을 확률보다 틀렸을 때 얼마나 잃는지가 중요합니다. 종목보다 비중을 먼저 정하십시오.",
        "이 종목이 당신 계좌에 '다른 움직임'을 더해줍니까? 비슷한 걸 또 사는 건 분산이 아닙니다."])


def _marks(c, r):
    fear = str(_g(c, "regime_trend")) == "역풍" or str(_g(c, "regime_vol")) == "고"
    if _g(c, "mode") == "hold":
        if fear:
            return "보유", r.choice([
                "모두가 비관할 때 위험은 이미 많이 빠져나가 있습니다. 공포 속의 투매는 대개 최악의 거래였습니다.",
                "'지금 사이클의 어디쯤인가'를 물으세요. 공포 구간에서의 매도는 사이클의 바닥을 남에게 선물하는 일입니다."])
        return "보유", "가장 큰 위험은 '위험이 없다는 믿음'에서 옵니다. 지금처럼 긴장감 있는 보유가 차라리 건강합니다."
    if fear:
        return "긍정", "싸게 사는 것이 유일하게 믿을 만한 수익의 원천입니다. 공포가 가격을 깎아주는 지금이 숙제할 시간입니다."
    return "중립", "지금 가격에 무엇이 반영돼 있습니까? 남들과 같은 생각이면 수익도 평균입니다."


GURUS = [
    ("워런 버핏", "🏛️", "가치·장기", _buffett),
    ("피터 린치", "🔍", "스토리·실적", _lynch),
    ("제시 리버모어", "📉", "추세·손절", _livermore),
    ("앙드레 코스톨라니", "🎭", "역발상·인내", _kostolany),
    ("레이 달리오", "⚖️", "분산·리스크", _dalio),
    ("하워드 막스", "🌊", "사이클·심리", _marks),
]

DISCLAIMER = ("실제 인물·AI·자문이 아니라 각 인물의 공개된 투자 철학을 조건문으로 "
              "코드화한 가상 코멘트입니다. 일부러 서로 다른 결론이 나오게 설계돼 "
              "있으며(쏠림 방지), 최종 판단과 책임은 본인에게 있습니다.")


def comments(ctx: dict) -> list[dict]:
    """[{guru, icon, style, stance, emoji, line}] — 같은 날 같은 종목이면 동일."""
    out = []
    for name, icon, style, fn in GURUS:
        r = random.Random(f"{_date.today()}|{ctx.get('code', '')}|{name}")
        try:
            stance, line = fn(ctx, r)
            out.append({"guru": name, "icon": icon, "style": style,
                        "stance": stance, "emoji": STANCE_EMOJI.get(stance, ""),
                        "line": line})
        except Exception:
            continue
    return out


def tally(cs: list[dict]) -> str:
    """'6인 의견: 🔻 정리 1 · ⏸ 관망 2 · 💎 보유 3' 형태 집계."""
    order = ["정리", "관망", "보유", "긍정", "중립", "부정"]
    cnt: dict[str, int] = {}
    for c in cs:
        cnt[c["stance"]] = cnt.get(c["stance"], 0) + 1
    parts = [f"{STANCE_EMOJI[s]} {s} {cnt[s]}" for s in order if cnt.get(s)]
    return f"{len(cs)}인 의견: " + " · ".join(parts)


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

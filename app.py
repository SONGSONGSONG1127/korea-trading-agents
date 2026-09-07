# -*- coding: utf-8 -*-
"""
한국 주식 멀티 에이전트 분석 (TradingAgents 스타일) v4
- 🔍 종목 분석: 종목코드 입력 → 4-에이전트 파이프라인 상세 분석
- 🏆 오늘의 후보 종목: 전 시장 2단계 스크리닝 → 합산 점수 랭킹 → 클릭 시 상세로 이동

실행:  streamlit run app.py
"""
import re

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from agents import (backdata_agent, backtest, chart_tutor, community, discount_agent, fund_agent,
                    fundamental_agent, news_agent, portfolio_agent, recommend_agent, scoring,
                    screener, stock_search, strategy_agent, technical_agent, track_record,
                    us_data, us_fundamental, us_screener)

st.set_page_config(
    page_title="K-TradingAgents | 한국 주식 멀티 에이전트 분석",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    .agent-card {
        border: 1px solid rgba(128,128,128,0.25);
        border-radius: 12px;
        padding: 1rem 1.2rem;
        margin-bottom: 0.8rem;
        background: rgba(128,128,128,0.05);
    }
    .agent-title { font-weight: 700; font-size: 1.05rem; margin-bottom: 0.4rem; }
    .opinion-buy  { color: #d32f2f; font-weight: 800; font-size: 1.6rem; }
    .opinion-hold { color: #f9a825; font-weight: 800; font-size: 1.6rem; }
    .opinion-sell { color: #1565c0; font-weight: 800; font-size: 1.6rem; }
    .rank-row { border-bottom: 1px solid rgba(128,128,128,0.15); padding: 0.35rem 0; }
    .fund-row { display: flex; gap: 1rem; flex-wrap: wrap; font-size: 0.9rem; }
    .fund-chip {
        background: rgba(128,128,128,0.1);
        border-radius: 8px;
        padding: 0.2rem 0.6rem;
    }
    .disclaimer {
        font-size: 0.8rem; color: gray;
        border-top: 1px solid rgba(128,128,128,0.3);
        padding-top: 0.6rem; margin-top: 1.2rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

UP_COLOR = "#d32f2f"
DOWN_COLOR = "#1565c0"

# ── 접속 코드 게이트 (secrets에 APP_PASSCODE 있을 때만 활성) ─────────────
_APP_PASS = ""
try:
    _APP_PASS = str(st.secrets.get("APP_PASSCODE", ""))
except Exception:
    pass
if _APP_PASS:
    if not st.session_state.get("authed"):
        st.title("🔒 TradingAgents")
        _pw = st.text_input("접속 코드", type="password", key="gate_pw")
        if _pw:
            if _pw == _APP_PASS:
                st.session_state["authed"] = True
                st.rerun()
            else:
                st.error("접속 코드가 올바르지 않습니다.")
        st.stop()

MODE_DETAIL    = "🔍 종목 분석"
MODE_SCREEN    = "🏆 오늘의 후보 종목"
MODE_PORTFOLIO = "💼 포트폴리오"
MODE_BACKDATA  = "📅 백데이터 검증"
MODE_FUND      = "🏦 펀드 시뮬레이션"
MODE_DISCOUNT  = "💎 할인찬스"
MODE_TRACK     = "📜 트랙레코드"

ss = st.session_state


@st.cache_resource
def _global_store() -> dict:
    """앱 프로세스 수준의 결과 보관소.

    브라우저 뒤로가기·새로고침으로 Streamlit 세션이 끊겨도, 앱 프로세스가 살아 있는 동안
    마지막 스크리닝/시뮬레이션 결과를 여기서 복원한다. (앱 재시작 시엔 초기화)
    """
    return {}


_PERSIST_KEYS = ("results", "screener", "bd_result", "fund_result", "dc_result",
                 "last_analyzed", "us_screener", "market", "us_results", "us_last")

ss.setdefault("results", {})
ss.setdefault("last_analyzed", None)
ss.setdefault("screener", None)
ss.setdefault("bt_open", None)      # 현재 열린 백테스트 종목코드
ss.setdefault("bt_cache", {})       # {code: run_multiperiod_for_code 결과}
ss.setdefault("pf_quick_add", None) # 포트폴리오 빠른 추가 대상 코드
ss.setdefault("bd_result", None)    # 백데이터 검증 결과 캐시
ss.setdefault("fund_result", None)  # 펀드 시뮬레이션 결과 캐시
ss.setdefault("fund_logs", None)    # 펀드 로그 조회 캐시
ss.setdefault("dc_result", None)    # 할인찬스 결과 캐시
ss.setdefault("us_screener", None)  # 미국 스크리너 결과 캐시
ss.setdefault("us_results", {})     # 미국 종목분석 캐시 {ticker: {...}}
ss.setdefault("us_last", None)      # 마지막 분석 티커
ss.setdefault("market", "KR")       # "KR" | "US"
if "code_input" not in ss:
    ss.code_input = "005930"
if "mode" not in ss:
    ss.mode = MODE_DETAIL

# ── 세션 복원/보관: 재접속해도 마지막 결과 유지 ─────────────────────────
_store = _global_store()
for _k in _PERSIST_KEYS:
    if not ss.get(_k) and _store.get(_k):
        ss[_k] = _store[_k]          # 새 세션 → 이전 결과 복원
for _k in _PERSIST_KEYS:
    if ss.get(_k):
        _store[_k] = ss[_k]          # 현재 결과를 보관소에 동기화
_store["results"] = ss.results       # dict는 참조 공유 → 이후 분석 결과 자동 반영
_store["us_results"] = ss.us_results


def goto_detail(code: str) -> None:
    if ss.get("mode") != MODE_DETAIL:
        ss["nav_from"] = ss.get("mode")   # 돌아가기용: 출발한 모드 기억
    ss["_nav_jump"] = True
    ss["mode"] = MODE_DETAIL
    ss["code_input"] = code
    ss["pending"] = code
    ss["kr_candidates"] = None


def nav_go_back() -> None:
    """점프해 온 모드로 복귀."""
    target = ss.pop("nav_from", None)
    if target:
        ss["_nav_jump"] = True
        ss["mode"] = target


def goto_us_detail(ticker: str) -> None:
    if ss.get("mode") != MODE_DETAIL:
        ss["nav_from"] = ss.get("mode")
    ss["_nav_jump"] = True
    ss["mode"] = MODE_DETAIL
    ss["us_code_input"] = ticker
    ss["us_pending"] = ticker
    ss["us_candidates"] = None


def _set_market(m: str) -> None:
    ss["market"] = m


# ── 사이드바 ────────────────────────────────────────────────────────────
with st.sidebar:
    _mk1, _mk2 = st.columns(2)
    _mk1.button("🇰🇷 K-Trading", use_container_width=True,
                type="primary" if ss.market == "KR" else "secondary",
                on_click=_set_market, args=("KR",))
    _mk2.button("🇺🇸 USA-Trading", use_container_width=True,
                type="primary" if ss.market == "US" else "secondary",
                on_click=_set_market, args=("US",))
    if ss.market == "KR":
        st.title("📈 K-TradingAgents")
        st.caption("멀티 에이전트 한국 주식 분석 v4")
    else:
        st.title("🇺🇸 USA-TradingAgents")
        st.caption("멀티 에이전트 미국 주식 분석 (S&P500) v1")

    mode = st.radio("모드", [MODE_DETAIL, MODE_SCREEN, MODE_PORTFOLIO, MODE_BACKDATA,
                            MODE_FUND, MODE_DISCOUNT, MODE_TRACK], key="mode")
    # 수동 모드 전환 시 '돌아가기' 목적지 무효화 (버튼 점프 직후 rerun은 예외)
    if ss.pop("_nav_jump", False):
        pass
    elif mode != ss.get("_last_mode") and ss.get("nav_from"):
        ss["nav_from"] = None
    ss["_last_mode"] = mode
    st.divider()

    if mode == MODE_DETAIL:
        scan_btn = False
        if ss.market == "US":
            run_btn = False
            us_code_in = st.text_input("티커 / 종목명", key="us_code_input",
                                       placeholder="예: AAPL 또는 팔란티어",
                                       help="티커(AAPL) 또는 한글·영문 이름(팔란티어, 엔비디아)으로 검색.")
            us_run_btn = st.button("🚀 분석 실행", type="primary", use_container_width=True)
            st.markdown(
                "**자주 찾는 종목**\n\n"
                "- Apple `AAPL`\n"
                "- NVIDIA `NVDA`\n"
                "- Microsoft `MSFT`\n"
                "- Tesla `TSLA`\n"
                "- Alphabet `GOOGL`"
            )
        else:
            code_in = st.text_input("종목코드 / 종목명", key="code_input",
                                    help="코드(005930) 또는 이름(삼성전자)으로 검색. "
                                         "부분 입력(삼성전)도 가능 — 후보 목록에서 선택.")
            run_btn = st.button("🚀 에이전트 분석 실행", type="primary", use_container_width=True)
            st.markdown(
                "**자주 찾는 종목**\n\n"
                "- 삼성전자 `005930`\n"
                "- SK하이닉스 `000660`\n"
                "- 현대차 `005380`\n"
                "- NAVER `035420`\n"
                "- 카카오 `035720`"
            )
    elif mode == MODE_SCREEN:
        run_btn = False
        if ss.market == "US":
            scan_btn = False
            us_liq = st.slider("거래대금($) 상위 필터 수", 50, 500, 200, 10, key="us_liq")
            us_top = st.slider("기술점수 상위 표시 수", 10, 50, 20, 5, key="us_top")
            us_scan_btn = st.button("📡 S&P500 스캔", type="primary", use_container_width=True)
            st.caption("S&P500 전 종목을 일괄 다운로드(yfinance) 후 기술점수 랭킹. 스크래핑 없음, 약 30~60초.")
        else:
            n_liq = st.slider("1단계: 거래대금 상위 종목 수", 50, 300, 200, 10,
                              help="네이버 시총 상위 목록에서 거래대금 순으로 자르는 1차 유동성 필터")
            n_full = st.slider("2단계: 풀 분석 종목 수", 3, 30, 10,
                               help="1단계 기술 점수 상위 종목만 뉴스+펀더멘탈 포함 풀 분석")
            scan_btn = st.button("📡 오늘의 후보 스캔", type="primary", use_container_width=True)
            st.caption(
                "1단계는 종목당 요청 1번(차트 API)이라 가볍고, "
                "뉴스·펀더멘탈 크롤링이 필요한 풀 분석은 상위 종목에만 실행됩니다. LLM 토큰은 쓰지 않습니다."
            )
    elif mode == MODE_PORTFOLIO:
        scan_btn = False
        run_btn   = False
        st.caption("Google Sheets에 저장된 매수 포지션을 관리합니다.")
        st.caption("손절선 = 매수가 − 1.5×ATR  |  목표가 = 매수가 + 2.0×ATR")
    elif mode == MODE_BACKDATA:
        from datetime import date, timedelta
        scan_btn = False
        run_btn  = False
        st.markdown("**과거 날짜 설정**")
        bd_date = st.date_input(
            "시뮬레이션 날짜",
            value=date.today() - timedelta(days=365),
            min_value=date.today() - timedelta(days=950),
            max_value=date.today() - timedelta(days=10),
            key="bd_date",
            help="이 날짜 기준으로 스크리너를 돌렸다면 어떤 종목이 나왔을지 시뮬레이션합니다. "
                 "1년 수익률을 보려면 최소 1년 이전 날짜를 선택하세요.",
        )
        bd_universe = st.slider("탐색 종목 수 (거래대금 상위)", 30, 500, 150, 10,
                                key="bd_universe",
                                help="코스피+코스닥 거래대금 상위 N종목 탐색. 클수록 정확하지만 느림.")
        bd_top = st.slider("최종 상위 종목 수", 5, 30, 10, 1,
                           key="bd_top",
                           help="기술점수 상위 K종목의 이후 수익률을 표시")
        bd_dart = st.checkbox(
            "펀더멘탈 포함 (DART)",
            value=False,
            key="bd_dart",
            help=f"상위 종목에 DART 사업보고서 기반 F-Score·ROE·부채비율 추가. "
                 f"종목당 API 호출이 추가되어 느려집니다.",
        )
        bd_btn = st.button("📅 백데이터 시뮬레이션 실행", type="primary", use_container_width=True)
        st.caption(
            "1년 수익률은 최소 1년 이전 날짜 선택 시 표시됩니다. "
            "⚠️ 생존 편향: 현재 상장 종목 기준."
        )
    elif mode == MODE_FUND:
        from datetime import date, timedelta
        scan_btn = False
        run_btn  = False
        st.markdown("**펀드 설정**")
        _period_opts = {
            "3주": 21, "1달": 30, "2달": 61, "3달": 91, "6달": 182,
            "9달": 274, "1년": 365, "1.5년": 548, "2년": 700,
        }
        fd_period = st.select_slider(
            "운용 기간 (설정일 → 오늘)",
            options=list(_period_opts), value="1년", key="fd_period",
            help="이 기간만큼 과거에 펀드를 설정했다면 오늘까지 어떻게 운용됐을지 시뮬레이션합니다. "
                 "3주~1달 단기 검증은 리밸런싱 주기를 1주로 두세요.",
        )
        fd_start = date.today() - timedelta(days=_period_opts[fd_period])

        fd_custom = st.text_input(
            "특정 설정일 직접 입력 (선택)", value="", key="fd_custom",
            placeholder="예: 2026-02-25",
            help="YYYY-MM-DD 형식. 입력하면 위 기간 선택보다 우선합니다.",
        )
        if fd_custom.strip():
            _lo = date.today() - timedelta(days=700)
            _hi = date.today() - timedelta(days=15)
            try:
                _d = pd.to_datetime(fd_custom.strip()).date()
                if _lo <= _d <= _hi:
                    fd_start = _d
                else:
                    st.warning(f"설정일은 {_lo} ~ {_hi} 범위여야 합니다. 기간 선택값을 사용합니다.")
            except (ValueError, TypeError):
                st.warning("날짜 형식이 올바르지 않습니다 (예: 2026-02-25). 기간 선택값을 사용합니다.")
        st.caption(f"📅 설정일: **{fd_start}** → 오늘")
        fd_universe = st.slider("탐색 종목 수 (거래대금 상위)", 50, 300, 100, 10, key="fd_universe",
                                help="리밸런싱 시점마다 그 시점의 20일 평균 거래대금 상위 N종목을 "
                                     "다시 탐색해 유니버스를 재구성합니다.")
        fd_top = st.slider("편입 종목 수", 3, 20, 10, 1, key="fd_top",
                           help="진단 결과 5종목 집중은 개별 종목 노이즈가 커서 10~20종목 분산이 더 안정적입니다.")
        fd_rebal = st.selectbox("리밸런싱 주기", list(fund_agent.REBALANCE_OPTIONS), index=2, key="fd_rebal",
                                help="보유 종목은 점수 랭크가 편입수×3 밖으로 밀릴 때만 교체(버퍼 규칙)되어 "
                                     "회전율과 거래비용을 억제합니다.")
        fd_weight = st.selectbox(
            "비중 방식", list(fund_agent.WEIGHT_LABELS.values()), key="fd_weight",
            help="균등: DeMiguel et al.(2009) 1/N — 기본이자 기준선 · "
                 "역변동성: 리스크 패리티(Maillard et al. 2010), 변동성 낮은 종목에 더 배분 · "
                 "점수비례: 기술점수에 비례 배분(모멘텀 공격형)",
        )
        fd_voltgt = st.checkbox(
            "변동성 타겟팅 (연 15%)", value=False, key="fd_voltgt",
            help="Moreira & Muir (2017): KOSPI 20일 변동성이 목표를 넘으면 주식 비중을 줄이고 현금 보유. 하락장 방어용.",
        )
        fd_stop = st.checkbox(
            "손절 서킷브레이커 (−2.5×ATR)", value=False, key="fd_stop",
            help="리밸런싱일 사이에도 매일 점검해, 종목이 편입가 − 2.5×ATR을 하회하면 즉시 현금화. "
                 "추세 하락장 방어용이지만 V자 반등 장세에선 휩쏘 비용이 발생할 수 있습니다 (Kaminski & Lo 2014).",
        )
        fd_btn = st.button("🏦 펀드 시뮬레이션 실행", type="primary", use_container_width=True)
        st.caption(
            "거래비용 편도 0.3% 반영 (수수료+세금+슬리피지) · 기준가 1,000원 시작 · "
            "⚠️ 생존 편향: 현재 상장 종목 기준."
        )
    elif mode == MODE_DISCOUNT:
        scan_btn = False
        run_btn  = False
        st.markdown("**할인 탐색 설정**")
        dc_per_max = st.slider("1차 저PER 컷 (배)", 8, 25, 15, 1, key="dc_per_max",
                               help="시총 상위 목록의 PER이 이 값 이하(흑자)인 종목만 정밀 분석 후보로.")
        dc_scan = st.slider("정밀 분석 종목 수", 20, 60, 40, 5, key="dc_scan",
                            help="후보 중 PER 낮은 순으로 이 수만큼 재무·차트 정밀 분석. 종목당 요청 ~4번이라 느립니다.")
        dc_top = st.slider("최종 표시 수", 10, 30, 20, 5, key="dc_top")
        dc_btn = st.button("💎 할인찬스 탐색", type="primary", use_container_width=True)
        st.caption(
            "저PER × 재무건전성(F-Score) × 안전마진(52주 할인+바닥 안정화) 조합. "
            "근거: Piotroski(2000), Fama-French(1992). 소요 1~2분."
        )
    else:  # MODE_TRACK
        scan_btn = False
        run_btn  = False
        st.caption(
            "매일 아침 브리핑이 자동 기록한 top5 종목들이 이후 실제로 어떻게 갔는지 채점합니다. "
            "표본이 쌓일수록 '이 스크리너를 믿을 근거'가 데이터로 완성됩니다."
        )
        tr_btn = st.button("📜 성과 채점 실행", type="primary", use_container_width=True)


# ── 파이프라인 실행 ─────────────────────────────────────────────────────
def analyze(code: str):
    with st.status("에이전트 파이프라인 실행 중...", expanded=True) as status:
        st.write("🗞️ **Agent 1** — 네이버 증권 뉴스/공시 수집 중...")
        news = news_agent.run(code)
        st.write(f"→ {news.stock_name}({news.code}) 기사/공시 {news.raw_count}건 → {len(news.items)}개 사건")

        st.write("📊 **Agent 2** — 시세 수집, 4축(추세/모멘텀/거래량/위치) 분석 중...")
        tech = technical_agent.run(code)
        st.write("→ " + (tech.error or tech.summary))

        st.write("📋 **Agent 3** — 재무제표·펀더멘탈 분석 중...")
        try:
            fund = fundamental_agent.run(code, stock_name=news.stock_name)
            st.write("→ " + (fund.error or fund.summary))
        except Exception as e:
            fund = None
            st.write(f"→ 펀더멘탈 수집 실패 (기술 점수만 사용): {e}")

        st.write("🧠 **Agent 4** — 종합 판단 및 매매 가격 산출 중...")
        strat = strategy_agent.run(news, tech, fund)

        st.write("💬 커뮤니티 관심 지표 수집 중... (참고용)")
        try:
            comm = community.get(code)
        except Exception:
            comm = None
        status.update(label="분석 완료 ✅", state="complete", expanded=False)
    return news, tech, fund, strat, comm


# ── 상세 분석 렌더링 ────────────────────────────────────────────────────
def score_color(score: int) -> str:
    if score >= 75:
        return UP_COLOR
    if score >= 60:
        return "#ef6c00"
    if score >= 40:
        return "#f9a825"
    if score >= 25:
        return "gray"
    return DOWN_COLOR


def render_chart_tutor(df_enriched, unit: str = "원") -> None:
    """차트 아래에 붙는 해석 + 공부 콘텐츠 (국장·미장 공통)."""
    try:
        items = chart_tutor.interpret(df_enriched, unit=unit)
    except Exception:
        return
    with st.expander("🧭 지금 차트 읽기 — 해석과 공부 포인트", expanded=True):
        for it in items:
            st.markdown(f"**{it['title']}** · {it['state']}")
            st.markdown(it["read"])
            st.caption(f"💡 {it['tip']}")
    with st.expander("📚 차트 공부방 — 지표 기초부터 함정까지"):
        for _t, _body in chart_tutor.LESSONS:
            st.markdown(f"**{_t}**")
            st.caption(_body)


def _fund_grade_color(grade: str) -> str:
    return {
        "매우 우량": UP_COLOR,
        "우량": "#ef6c00",
        "보통": "#f9a825",
        "취약": "gray",
        "위험": DOWN_COLOR,
        "미확인": "gray",
    }.get(grade, "gray")


def render_fundamental(fund) -> None:
    """펀더멘탈 섹션 렌더링."""
    if fund is None or fund.error:
        msg = fund.error if fund else "펀더멘탈 데이터 없음"
        st.warning(f"📋 펀더멘탈 데이터를 가져오지 못했습니다: {msg}")
        return

    color = _fund_grade_color(fund.fund_grade)
    dart_badge = " 🔗 DART" if getattr(fund, "dart_available", False) else ""

    # 데이터 품질 레이블
    dq_map = {
        "완전(DART)": "✅ 완전 (DART 재무제표)",
        "완전":        "✅ 완전",
        "부분":        "⚠️ 부분",
        "기본":        "⚠️ 기본 지표만",
    }
    dq_label = dq_map.get(getattr(fund, "data_quality", ""), "")

    # 점수 카드
    st.markdown(
        f'<div class="agent-card">'
        f'<div class="agent-title">📋 펀더멘탈 분석 (밸류 · 품질 · 성장){dart_badge}</div>'
        f'<div style="font-size:2rem; font-weight:800; color:{color}; line-height:1.1;">'
        f'{fund.fund_score}<span style="font-size:1rem; color:gray;"> / 100 · {fund.fund_grade}</span></div>'
        f'<div style="font-size:0.85rem; margin-top:0.3rem;">'
        f'밸류에이션 <b>{fund.valuation_score}</b>/50 &nbsp;·&nbsp; '
        f'품질(ROE·F-Score) <b>{fund.quality_score}</b>/30 &nbsp;·&nbsp; '
        f'성장·수익 <b>{fund.income_score}</b>/20</div>'
        f'<div style="font-size:0.8rem; margin-top:0.3rem; color:gray;">데이터 {dq_label}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # 주요 지표 칩
    chips = []
    if fund.per is not None:
        per_color = UP_COLOR if fund.per < 12 else (DOWN_COLOR if fund.per > 25 else "#f9a825")
        rel = ""
        if fund.sector_per and fund.sector_per > 0:
            disc = (fund.sector_per - fund.per) / fund.sector_per * 100
            rel = f" (업종 {fund.sector_per:.1f}배 대비 {disc:+.0f}%)"
        chips.append(f'<span class="fund-chip">PER <b style="color:{per_color}">{fund.per:.1f}배</b>{rel}</span>')
    if fund.pbr is not None:
        pbr_color = UP_COLOR if fund.pbr < 1.0 else (DOWN_COLOR if fund.pbr > 3.0 else "#f9a825")
        chips.append(f'<span class="fund-chip">PBR <b style="color:{pbr_color}">{fund.pbr:.2f}배</b></span>')
    if fund.pbr and fund.pbr > 0 and fund.roe and fund.roe > 0:
        # 고든 성장모형: 적정 PBR = ROE / 요구수익률(8%)
        _fair = fund.roe / 8.0
        _disc = (_fair - fund.pbr) / _fair * 100
        fp_color = UP_COLOR if _disc >= 20 else (DOWN_COLOR if _disc <= -20 else "#f9a825")
        chips.append(f'<span class="fund-chip">적정PBR({_fair:.1f}) 대비 '
                     f'<b style="color:{fp_color}">{_disc:+.0f}%</b></span>')
    if fund.roe is not None:
        roe_color = UP_COLOR if fund.roe >= 15 else (DOWN_COLOR if fund.roe < 5 else "#f9a825")
        chips.append(f'<span class="fund-chip">ROE <b style="color:{roe_color}">{fund.roe:.1f}%</b></span>')
    if getattr(fund, "roa", None) is not None:
        roa_color = UP_COLOR if fund.roa >= 5 else (DOWN_COLOR if fund.roa < 0 else "#f9a825")
        chips.append(f'<span class="fund-chip">ROA <b style="color:{roa_color}">{fund.roa:.1f}%</b></span>')
    if getattr(fund, "debt_ratio", None) is not None:
        dr_color = DOWN_COLOR if fund.debt_ratio > 200 else ("#f9a825" if fund.debt_ratio > 100 else UP_COLOR)
        chips.append(f'<span class="fund-chip">부채비율 <b style="color:{dr_color}">{fund.debt_ratio:.0f}%</b></span>')
    if getattr(fund, "op_margin", None) is not None:
        om_color = UP_COLOR if fund.op_margin >= 15 else ("#f9a825" if fund.op_margin >= 5 else DOWN_COLOR)
        chips.append(f'<span class="fund-chip">영업이익률 <b style="color:{om_color}">{fund.op_margin:.1f}%</b></span>')
    if getattr(fund, "revenue_growth", None) is not None:
        rg_color = UP_COLOR if fund.revenue_growth >= 10 else (DOWN_COLOR if fund.revenue_growth < 0 else "#f9a825")
        chips.append(f'<span class="fund-chip">매출성장 <b style="color:{rg_color}">{fund.revenue_growth:+.1f}%</b></span>')
    if getattr(fund, "op_profit_growth", None) is not None:
        og_color = UP_COLOR if fund.op_profit_growth >= 15 else (DOWN_COLOR if fund.op_profit_growth < 0 else "#f9a825")
        chips.append(f'<span class="fund-chip">영업이익성장 <b style="color:{og_color}">{fund.op_profit_growth:+.1f}%</b></span>')
    if fund.div_yield is not None:
        dy_color = UP_COLOR if fund.div_yield >= 3 else ("gray" if fund.div_yield >= 1 else DOWN_COLOR)
        chips.append(f'<span class="fund-chip">배당 <b style="color:{dy_color}">{fund.div_yield:.2f}%</b></span>')

    if chips:
        st.markdown(f'<div class="fund-row">{"".join(chips)}</div>', unsafe_allow_html=True)
        st.markdown("")

    # ── 재무 자연어 해석 (규칙 기반) ──────────────────────────────────────
    _nar = fundamental_agent.narrative(fund)
    if _nar:
        st.info(f"🧾 **재무 해석** — {_nar}")

    # Piotroski F-Score (DART 있을 때)
    f_score = getattr(fund, "f_score", None)
    if f_score is not None:
        f_color = UP_COLOR if f_score >= 7 else (DOWN_COLOR if f_score <= 2 else "#f9a825")
        f_details = getattr(fund, "f_details", [])
        with st.expander(
            f"🏅 Piotroski F-Score: **{f_score}/9** — "
            + ("우량" if f_score >= 7 else ("위험" if f_score <= 2 else "보통"))
        ):
            st.caption(
                "Piotroski (2000, JAR): 수익성(4) + 재무건전성(3) + 운영효율(2) "
                "총 9개 이진 지표. 8~9점=우량, 6~7=양호, 4~5=보통, ≤3=주의."
            )
            for d in f_details:
                st.markdown(d)

    # ROE 분기 추이
    if fund.roe_quarters:
        with st.expander(f"📈 ROE 분기 추이 ({len(fund.roe_quarters)}분기, 최신→과거)"):
            roe_df = pd.DataFrame(
                [{"분기": f"Q-{i}" if i > 0 else "최근", "ROE(%)": v}
                 for i, v in enumerate(fund.roe_quarters)]
            )
            st.dataframe(roe_df, hide_index=True, use_container_width=True)


def render_analysis(news, tech, fund, strat, comm=None):
    st.subheader(f"{news.stock_name} ({news.code})")

    df = tech.df
    prev_close = float(df["close"].iloc[-2]) if len(df) >= 2 else tech.price
    change = tech.price - prev_close
    change_pct = change / prev_close * 100 if prev_close else 0

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("현재가", f"{tech.price:,.0f}원", f"{change:+,.0f}원 ({change_pct:+.2f}%)")
    c2.metric("RSI (14)", f"{tech.rsi:.1f}")
    c3.metric("ATR", f"{tech.atr:,.0f}원", f"{tech.atr_pct:.1%} 진폭", delta_color="off")
    c4.metric("기술 점수", f"{strat.score100}점", strat.grade, delta_color="off")
    c5.metric("펀더멘탈", f"{strat.fund_score}점", strat.fund_grade, delta_color="off")
    c6.metric("합산 점수", f"{strat.combined_score}점",
              help="기술 60% + 펀더멘탈 40% 합산 — 매수 임계 55점(기본)", delta_color="off")

    badges = []
    if tech.regime:
        icon = {"순풍": "🟢", "역풍": "🔴", "중립": "⚪"}[tech.regime]
        badges.append(f"{icon} 시장 레짐: **{tech.regime}** ({tech.regime_evidence})")
    for f in tech.flags:
        badges.append(f"🏳️ {f}")
    if news.veto:
        badges.append("🚫 강한 악재 공시 — 매수 의견 베토 발동")
    if badges:
        st.markdown(" · ".join(badges))

    st.markdown("### 🧠 Agent 4 — 최종 전략")
    left, right = st.columns([1, 2])
    with left:
        sub = f'<div style="color:gray; font-size:0.85rem;">{strat.reason_code}</div>' if strat.reason_code else ""
        color = score_color(strat.combined_score)
        st.markdown(
            f'<div class="agent-card"><div class="agent-title">합산 투자 매력 점수</div>'
            f'<div style="font-size:2.6rem; font-weight:800; color:{color}; line-height:1.1;">'
            f'{strat.combined_score}<span style="font-size:1.1rem; color:gray;"> / 100</span></div>'
            f'<div style="font-size:0.9rem; margin-top:0.2rem;">'
            f'기술 <b>{strat.score100}</b> · 펀더멘탈 <b>{strat.fund_score}</b></div>'
            f'<div style="margin-top:0.3rem;">참고 의견: <b style="color:{color}">{strat.opinion}</b></div>{sub}'
            f'<div style="margin-top:0.3rem; font-size:0.85rem;">{strat.confidence}</div></div>',
            unsafe_allow_html=True,
        )

        m = scoring.meta()
        band = strat.band or {}
        st.markdown(
            f'<div class="agent-card" style="font-size:0.85rem;">'
            f'<div class="agent-title" style="font-size:0.95rem;">📖 점수 산출 정보</div>'
            f'<div>① 기술 원점수 <b>{strat.total_score:+.2f}</b> = 4축(추세 35/모멘텀 25/거래량 25/위치 15%) '
            f'가중 합 × 합의 배수 {tech.confluence_mult:.2f}</div>'
            f'<div>② 기술 <b>{strat.score100}점</b> = 과거 {m["n_samples"]:,} 종목-일 분포 상위 {100 - strat.score100}%</div>'
            f'<div>③ 펀더멘탈 <b>{strat.fund_score}점</b> = Piotroski F-스코어(0–9) + 밸류에이션(Graham/Fama-French) + 성장성</div>'
            f'<div>④ 합산 <b>{strat.combined_score}점</b> = 기술 60% + 펀더멘탈 40% — 매수 임계 55점</div>'
            f'<div style="margin-top:0.3rem;">이 점수 구간({band.get("lo", "?")}~{band.get("hi", "?")}점)의 과거 {m["horizon"]}일 후: '
            f'승률 <b>{band.get("win", 0):.0%}</b>, 평균 <b>{band.get("avg", 0):+.1%}</b> '
            f'(기준선 {m["baseline_win"]:.0%}/{m["baseline_avg"]:+.1%})</div>'
            f'<div style="color:gray;">뉴스·커뮤니티는 점수 미반영(참고용). 과거 통계는 성과를 보장하지 않습니다.</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        b1, b2 = st.columns(2)
        b1.metric("매수 희망가", f"{strat.buy_price:,.0f}원")
        b2.metric("손절가", f"{strat.stop_loss:,.0f}원",
                  f"{(strat.stop_loss / strat.buy_price - 1) * 100:.1f}%")
        b3, b4 = st.columns(2)
        b3.metric("1차 목표", f"{strat.target_price:,.0f}원",
                  f"{(strat.target_price / strat.buy_price - 1) * 100:+.1f}%")
        b4.metric("2차 목표", f"{strat.target2_price:,.0f}원",
                  f"{(strat.target2_price / strat.buy_price - 1) * 100:+.1f}%")
        st.caption(f"진입 근거: {strat.entry_basis}")
        st.caption(f"손절 근거: {strat.stop_basis}")

        st.markdown("**판단 기여도 (기술 4축)**")
        contrib_df = pd.DataFrame(
            [{"요인": k, "기여도(%)": v} for k, v in
             sorted(strat.contrib_pct.items(), key=lambda x: -x[1])]
        )
        st.dataframe(
            contrib_df, hide_index=True, use_container_width=True,
            column_config={"기여도(%)": st.column_config.ProgressColumn(
                "기여도(%)", min_value=0, max_value=100, format="%d%%")},
        )

    with right:
        order = ["결론", "주도 근거", "상충 신호", "무효화 조건", "뉴스 인용"]
        icons = {"결론": "⚖️", "주도 근거": "📌", "상충 신호": "⚡",
                 "무효화 조건": "🛑", "뉴스 인용": "🗞️"}
        body = ""
        for key in order:
            text = strat.sections.get(key, "")
            body += f'<div class="agent-title" style="margin-top:0.6rem;">{icons[key]} {key}</div>'
            body += "".join(f"<div>{line}</div>" for line in text.split("\n\n"))
        body = body.replace("**", "")
        st.markdown(f'<div class="agent-card">{body}</div>', unsafe_allow_html=True)
        st.success(strat.summary)

    # ── 펀더멘탈 섹션 ──────────────────────────────────────────────────
    st.markdown("### 📋 Agent 3 — 펀더멘탈 / 재무제표 분석")
    render_fundamental(fund)

    # ── 기술적 분석 차트 ───────────────────────────────────────────────
    st.markdown("### 📊 Agent 2 — 기술적 분석 (4축 Signal)")

    plot_df = df.tail(90)
    fig = make_subplots(
        rows=3, cols=1, shared_xaxes=True,
        row_heights=[0.6, 0.2, 0.2], vertical_spacing=0.03,
        subplot_titles=("일봉 / 이동평균선 / 볼린저밴드", "거래량", "RSI (14)"),
    )
    fig.add_trace(
        go.Scatter(x=plot_df["date"], y=plot_df["bb_upper"], name="BB 상단",
                   line=dict(width=0.8, color="rgba(128,128,128,0.5)"), mode="lines",
                   showlegend=False),
        row=1, col=1,
    )
    fig.add_trace(
        go.Scatter(x=plot_df["date"], y=plot_df["bb_lower"], name="볼린저밴드",
                   line=dict(width=0.8, color="rgba(128,128,128,0.5)"), mode="lines",
                   fill="tonexty", fillcolor="rgba(128,128,128,0.08)"),
        row=1, col=1,
    )
    fig.add_trace(
        go.Candlestick(
            x=plot_df["date"], open=plot_df["open"], high=plot_df["high"],
            low=plot_df["low"], close=plot_df["close"], name="일봉",
            increasing_line_color=UP_COLOR, decreasing_line_color=DOWN_COLOR,
            increasing_fillcolor=UP_COLOR, decreasing_fillcolor=DOWN_COLOR,
        ),
        row=1, col=1,
    )
    for col_name, label, color in [
        ("ma5", "MA5", "#7b1fa2"),
        ("ma20", "MA20", "#ef6c00"),
        ("ma60", "MA60", "#2e7d32"),
    ]:
        fig.add_trace(
            go.Scatter(x=plot_df["date"], y=plot_df[col_name], name=label,
                       line=dict(width=1.5, color=color), mode="lines"),
            row=1, col=1,
        )
    for price_lv, label, color, dash in [
        (strat.buy_price, "매수 희망가", "#f9a825", "dash"),
        (strat.stop_loss, "손절가", DOWN_COLOR, "dot"),
        (strat.target_price, "1차 목표", UP_COLOR, "dot"),
    ]:
        fig.add_hline(
            y=price_lv, line_dash=dash, line_color=color, line_width=1.2,
            annotation_text=f"{label} {price_lv:,.0f}", annotation_font_color=color,
            row=1, col=1,
        )
    vol_colors = [UP_COLOR if c >= o else DOWN_COLOR
                  for o, c in zip(plot_df["open"], plot_df["close"])]
    fig.add_trace(
        go.Bar(x=plot_df["date"], y=plot_df["volume"], name="거래량",
               marker_color=vol_colors, opacity=0.6),
        row=2, col=1,
    )
    fig.add_trace(
        go.Scatter(x=plot_df["date"], y=plot_df["vol_ma20"], name="거래량 MA20",
                   line=dict(width=1.2, color="#5e35b1"), mode="lines"),
        row=2, col=1,
    )
    fig.add_trace(
        go.Scatter(x=plot_df["date"], y=plot_df["rsi"], name="RSI",
                   line=dict(width=1.5, color="#5e35b1"), mode="lines"),
        row=3, col=1,
    )
    fig.add_hline(y=70, line_dash="dot", line_color=UP_COLOR, line_width=1, row=3, col=1)
    fig.add_hline(y=30, line_dash="dot", line_color=DOWN_COLOR, line_width=1, row=3, col=1)
    fig.update_layout(
        height=760, xaxis_rangeslider_visible=False,
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        margin=dict(t=60, b=20, l=10, r=10),
        hovermode="x unified",
    )
    fig.update_yaxes(gridcolor="rgba(128,128,128,0.15)")
    fig.update_xaxes(gridcolor="rgba(128,128,128,0.1)")
    st.plotly_chart(fig, use_container_width=True)

    render_chart_tutor(df, unit="원")

    sc1, sc2 = st.columns([1, 2])
    with sc1:
        st.markdown("**카테고리 점수** (합의 배수 ×{:.2f})".format(tech.confluence_mult))
        cat_df = pd.DataFrame(
            [{"카테고리": k, "가중치": technical_agent.CATEGORY_WEIGHTS[k], "점수": v}
             for k, v in tech.cat_scores.items()]
        )
        st.dataframe(cat_df, hide_index=True, use_container_width=True,
                     column_config={"점수": st.column_config.NumberColumn(format="%+.2f")})
    with sc2:
        st.markdown("**개별 신호와 증거**")
        sig_df = pd.DataFrame(
            [{"카테고리": s.category, "신호": s.name, "점수": s.score, "증거": s.evidence}
             for s in tech.signals]
        )
        st.dataframe(sig_df, hide_index=True, use_container_width=True,
                     column_config={"점수": st.column_config.NumberColumn(format="%+.2f")})

    if tech.backtest and tech.backtest.get("n_signals"):
        bt = tech.backtest
        with st.expander(f"🧪 간이 백테스트 — 최근 {bt['n_days']}거래일, 근사 점수 ≥ {bt['threshold']} 신호 {bt['n_signals']}건"):
            m1, m2, m3 = st.columns(3)
            m1.metric(f"{bt['horizon']}일 후 승률", f"{bt['win_rate']:.0%}")
            m2.metric("신호 평균 수익률", f"{bt['avg_return']:+.1%}")
            m3.metric("전체 구간 평균(기준선)", f"{bt['base_avg_return']:+.1%}")

    # ── 뉴스 섹션 ──────────────────────────────────────────────────────
    st.markdown("### 🗞️ Agent 1 — 뉴스 / 공시 (참고용)")
    st.caption("뉴스 감성은 점수에 반영되지 않습니다. 단, 유상증자·거래정지 등 강한 악재 공시(≤ -0.7)는 매수 의견을 베토합니다.")
    if news.error:
        st.warning(news.error)
    else:
        st.info(news.summary)
        for ev in news.events:
            fn = st.error if ev.sentiment <= -0.6 else st.warning if ev.sentiment < 0 else st.success
            fn(f"**[{ev.kind}] {ev.title}** ({ev.date}) — 점수 {ev.sentiment:+.1f}"
               + (f" · {ev.matched}" if ev.matched else ""))

        def emoji(s: float) -> str:
            if s >= 0.5:
                return "🔴🔴 강한 긍정"
            if s >= 0.2:
                return "🔴 긍정"
            if s <= -0.5:
                return "🔵🔵 강한 부정"
            if s <= -0.2:
                return "🔵 부정"
            return "⚪ 중립"

        table = pd.DataFrame(
            [{
                "구분": it.kind,
                "감성": emoji(it.sentiment),
                "점수": it.sentiment,
                "제목": it.title + (f" (유사 {it.dup_count}건)" if it.dup_count > 1 else ""),
                "근거 키워드": it.matched,
                "출처": it.source,
                "날짜": it.date,
                "링크": it.url,
            } for it in news.items]
        )
        st.dataframe(
            table, use_container_width=True, hide_index=True,
            column_config={
                "링크": st.column_config.LinkColumn("링크", display_text="열기"),
                "점수": st.column_config.NumberColumn(format="%+.1f"),
            },
        )

    st.markdown("### 💬 커뮤니티 관심 (참고)")
    if comm is not None:
        st.info("🔥 " + comm.note if (comm.search_rank or comm.board_ratio is None) else comm.note)
        st.caption(
            "네이버 금융 검색상위 30 + 종목토론실 게시글 활성도입니다. 관심 급증은 상승 재료일 수도, "
            "급락 후 반응일 수도 있어 방향성이 없으므로 점수에는 반영하지 않습니다."
        )
    else:
        st.caption("커뮤니티 데이터를 가져오지 못했습니다.")


def render_disclaimer():
    st.markdown(
        '<div class="disclaimer">⚠️ 본 프로그램은 교육·연구 목적의 규칙 기반 분석 도구이며, '
        "제시된 가격과 의견은 투자 권유가 아닙니다. 점수 가중치·임계값은 검증되지 않은 설정값이며 "
        "백테스트 통계는 근사치입니다. 투자의 최종 판단과 책임은 투자자 본인에게 있습니다. "
        "데이터 출처: 네이버 증권 (지연/오류 가능)</div>",
        unsafe_allow_html=True,
    )


# ── USA 마켓 디스패치 ───────────────────────────────────────────────────
if ss.market == "US" and mode in (MODE_BACKDATA, MODE_DISCOUNT):
    st.title(f"🇺🇸 {mode}")
    st.info(
        "이 모드의 미국 버전은 준비 중입니다. "
        "현재 USA-Trading은 **🔍 종목 분석 · 🏆 오늘의 후보 종목 · 💼 포트폴리오 · 🏦 펀드 시뮬레이션**을 지원합니다. "
        "국장 기능은 왼쪽 상단 🇰🇷 K-Trading에서 그대로 사용할 수 있어요."
    )

elif ss.market == "US" and mode == MODE_DETAIL:
    if ss.get("nav_from"):
        st.button(f"← {ss['nav_from']} 결과로 돌아가기", on_click=nav_go_back)
    st.title("🇺🇸 미국 종목 분석")
    st.caption("차트 · 기술 지표 · 멀티기간 백테스트 · 기업 정보 (뉴스·펀더멘탈 점수는 v2)")

    _target = None
    _p = ss.pop("us_pending", None)
    if _p:
        _target = _p
    elif us_run_btn and us_code_in.strip():
        _q = us_code_in.strip()
        if re.fullmatch(r"[A-Za-z.\-]{1,6}", _q):
            _target = _q
            ss.us_candidates = None
        else:
            # 한글·긴 이름 → 검색으로 티커 변환
            _cands = stock_search.search(_q, market="US")
            if len(_cands) == 1:
                _target = _cands[0]["code"]
                ss.us_candidates = None
            elif _cands:
                ss.us_candidates = {"query": _q, "items": _cands}
            else:
                st.warning(f"'{_q}' 검색 결과가 없습니다. 티커 또는 이름을 확인해 주세요.")
                ss.us_candidates = None

    _uc = ss.get("us_candidates")
    if _uc and not _target:
        st.markdown(f"**'{_uc['query']}' 검색 결과** — 분석할 종목을 선택하세요:")
        for c in _uc["items"]:
            st.button(f"🔍 {c['name']}  ·  {c['code']}  ·  {c['exchange']}",
                      key=f"uss_{c['code']}", on_click=goto_us_detail, args=(c["code"],))

    if _target:
        _tk = _target.strip().upper()
        if _tk not in ss.us_results or us_run_btn:
            with st.spinner(f"{_tk} 데이터 수신·분석 중... (차트·재무·기술신호)"):
                try:
                    _df = us_data.fetch_daily_prices(_tk, days=900)
                    _info = us_data.ticker_info(_tk)
                    _fund = us_fundamental.run(_tk)
                    _wiki = us_data.wiki_summary_kr(_info["name"])
                    _regime = us_data.market_regime()
                    _tech = technical_agent.run(_tk, df=_df.copy(), regime=_regime, unit="달러")
                    _enr = technical_agent.enrich(_df.copy())
                    _sc = backtest.score_series(_enr)
                    _last = _enr.iloc[-1]
                    _tech.df = None  # 세션 캐시 경량화 (df는 별도 저장)
                    ss.us_results[_tk] = {
                        "df":      _enr.tail(400).reset_index(drop=True),  # 지표 포함 (차트용)
                        "info":    _info,
                        "fund":    _fund,
                        "wiki":    _wiki,
                        "tech":    _tech,
                        "regime":  _regime,
                        "score":   float(_sc.iloc[-1]) if not pd.isna(_sc.iloc[-1]) else 0.0,
                        "periods": backtest.run_multiperiod(_enr),
                        "ind": {
                            "rsi":      float(_last["rsi"]),
                            "ma20_gap": float(_last["close"] / _last["ma20"] - 1),
                            "ma60_gap": float(_last["close"] / _last["ma60"] - 1) if pd.notna(_last["ma60"]) else None,
                            "macd_hist": float(_last["macd_hist"]),
                            "pct_b":    float(_last["pct_b"]),
                            "rvol":     float(_last["rvol"]),
                            "atr_pct":  float(_last["atr"] / _last["close"]),
                        },
                        "n_days":  len(_df),
                        "run_date": str(pd.Timestamp.now().date()),
                    }
                    ss.us_last = _tk
                except Exception as e:
                    st.error(f"{_tk} 분석 실패: {e}")
        else:
            ss.us_last = _tk

    _tk = ss.get("us_last")
    if _tk and _tk in ss.us_results:
        r = ss.us_results[_tk]
        info, ind, df_us = r["info"], r["ind"], r["df"]
        _sec = us_data.sector_kr(info["sector"]) if info["sector"] else "—"
        st.subheader(f"{info['name']} ({_tk})")
        st.caption(f"{_sec} · {info['industry'] or '—'} · {r['run_date']} 기준 · {r['n_days']}거래일 데이터")

        _close = float(df_us["close"].iloc[-1])
        _prev = float(df_us["close"].iloc[-2]) if len(df_us) >= 2 else _close
        _chg = (_close / _prev - 1) * 100
        c1, c2, c3, c4, c5, c6 = st.columns(6)
        c1.metric("현재가", f"${_close:,.2f}", f"{_chg:+.2f}%")
        c2.metric("기술점수", f"{r['score'] * 100:+.0f}")
        c3.metric("시총", f"${info['market_cap'] / 1e9:,.0f}B" if info["market_cap"] else "—")
        c4.metric("PER", f"{info['per']:.1f}" if info["per"] else "—")
        if info["hi52"] and info["lo52"] and info["hi52"] > info["lo52"]:
            _pos = (_close - info["lo52"]) / (info["hi52"] - info["lo52"])
            c5.metric("52주 위치", f"{_pos:.0%}")
        else:
            c5.metric("52주 위치", "—")
        c6.metric("배당", f"{info['div_yield']:.2f}%" if info["div_yield"] else "—")

        # ── 어떤 기업인가 (한글 요약 카드) ────────────────────────────
        if r.get("wiki"):
            st.markdown(
                f'<div class="agent-card"><div class="agent-title">🏢 어떤 기업?</div>'
                f'{r["wiki"]}</div>', unsafe_allow_html=True,
            )

        # ── 펀더멘탈 (미장식: 섹터 상대 · FCF · 주주환원) ─────────────
        _f = r.get("fund")
        if _f:
            st.markdown("### 📋 펀더멘탈 (섹터 상대 · 현금흐름 · 주주환원)")
            _gcolor = UP_COLOR if _f["score"] >= 60 else ("#f9a825" if _f["score"] >= 45 else DOWN_COLOR)
            st.markdown(
                f'<div class="agent-card">'
                f'<div class="agent-title">📋 미장식 펀더멘탈 점수</div>'
                f'<div style="font-size:1.7rem; font-weight:800; color:{_gcolor};">'
                f'{_f["score"]}<span style="font-size:1rem; color:gray;"> / 100 · {_f["grade"]}</span></div>'
                f'<div style="font-size:0.85rem; color:gray; margin-top:0.2rem;">'
                f'밸류에이션 <b>{_f["val_score"]}</b>/35 &nbsp;·&nbsp; '
                f'품질 <b>{_f["qual_score"]}</b>/35 &nbsp;·&nbsp; '
                f'성장·주주환원 <b>{_f["growth_score"]}</b>/30</div>'
                f'</div>', unsafe_allow_html=True,
            )
            _chips = []
            if _f["fwd_pe"]:
                if _f["pe_ratio"]:
                    _rel = (1 - _f["pe_ratio"]) * 100
                    _pc = UP_COLOR if _rel >= 10 else (DOWN_COLOR if _rel <= -10 else "#f9a825")
                    _chips.append(f'<span class="fund-chip">fwd PER <b style="color:{_pc}">{_f["fwd_pe"]:.1f}배</b> (섹터 {_f["sector_pe"]:.0f}배 대비 {_rel:+.0f}%)</span>')
                else:
                    _chips.append(f'<span class="fund-chip">fwd PER <b>{_f["fwd_pe"]:.1f}배</b></span>')
            if _f["ev_ebitda"]:
                _c = UP_COLOR if _f["ev_ebitda"] < 11 else (DOWN_COLOR if _f["ev_ebitda"] > 18 else "#f9a825")
                _chips.append(f'<span class="fund-chip">EV/EBITDA <b style="color:{_c}">{_f["ev_ebitda"]:.1f}</b></span>')
            if _f["fcf_yield"] is not None:
                _c = UP_COLOR if _f["fcf_yield"] >= 4 else (DOWN_COLOR if _f["fcf_yield"] < 1.5 else "#f9a825")
                _chips.append(f'<span class="fund-chip">FCF수익률 <b style="color:{_c}">{_f["fcf_yield"]:.1f}%</b></span>')
            if _f["peg"]:
                _c = UP_COLOR if _f["peg"] < 1.5 else (DOWN_COLOR if _f["peg"] > 2.5 else "#f9a825")
                _chips.append(f'<span class="fund-chip">PEG <b style="color:{_c}">{_f["peg"]:.2f}</b></span>')
            if _f["roe"] is not None:
                _c = UP_COLOR if _f["roe"] >= 15 else (DOWN_COLOR if _f["roe"] < 5 else "#f9a825")
                _chips.append(f'<span class="fund-chip">ROE <b style="color:{_c}">{_f["roe"]:.0f}%</b></span>')
            if _f["gross_m"] is not None:
                _c = UP_COLOR if _f["gross_m"] >= 50 else ("#f9a825" if _f["gross_m"] >= 30 else DOWN_COLOR)
                _chips.append(f'<span class="fund-chip">매출총이익률 <b style="color:{_c}">{_f["gross_m"]:.0f}%</b></span>')
            if _f["op_m"] is not None:
                _c = UP_COLOR if _f["op_m"] >= 20 else ("#f9a825" if _f["op_m"] >= 10 else DOWN_COLOR)
                _chips.append(f'<span class="fund-chip">영업마진 <b style="color:{_c}">{_f["op_m"]:.0f}%</b></span>')
            if _f["de"] is not None:
                _c = DOWN_COLOR if _f["de"] > 200 else ("#f9a825" if _f["de"] > 100 else UP_COLOR)
                _chips.append(f'<span class="fund-chip">D/E <b style="color:{_c}">{_f["de"]:.0f}%</b></span>')
            if _f["rev_g"] is not None:
                _c = UP_COLOR if _f["rev_g"] >= 8 else (DOWN_COLOR if _f["rev_g"] < 0 else "#f9a825")
                _chips.append(f'<span class="fund-chip">매출성장 <b style="color:{_c}">{_f["rev_g"]:+.0f}%</b></span>')
            if _f["sh_yield"] is not None:
                _c = UP_COLOR if _f["sh_yield"] >= 3 else ("#f9a825" if _f["sh_yield"] >= 1.5 else "gray")
                _chips.append(f'<span class="fund-chip">주주환원 <b style="color:{_c}">{_f["sh_yield"]:.1f}%</b></span>')
            if _chips:
                st.markdown(f'<div class="fund-row">{"".join(_chips)}</div>', unsafe_allow_html=True)
                st.markdown("")
            if _f["narrative"]:
                st.info(f"🧾 **재무 해석** — {_f['narrative']}")

        # ── 기술적 분석 (K와 동일한 3단 차트 + 4축 신호) ──────────────
        st.markdown("### 📊 기술적 분석 (4축 Signal)")
        _rg = r.get("regime")
        if _rg:
            st.caption(f"시장 레짐: **{_rg[0]}** — {_rg[1]}")

        plot_us = df_us.tail(90)
        fig_us = make_subplots(
            rows=3, cols=1, shared_xaxes=True,
            row_heights=[0.6, 0.2, 0.2], vertical_spacing=0.03,
            subplot_titles=("일봉 / 이동평균선 / 볼린저밴드", "거래량", "RSI (14)"),
        )
        fig_us.add_trace(
            go.Scatter(x=plot_us["date"], y=plot_us["bb_upper"], name="BB 상단",
                       line=dict(width=0.8, color="rgba(128,128,128,0.5)"), mode="lines",
                       showlegend=False), row=1, col=1)
        fig_us.add_trace(
            go.Scatter(x=plot_us["date"], y=plot_us["bb_lower"], name="볼린저밴드",
                       line=dict(width=0.8, color="rgba(128,128,128,0.5)"), mode="lines",
                       fill="tonexty", fillcolor="rgba(128,128,128,0.08)"), row=1, col=1)
        fig_us.add_trace(
            go.Candlestick(
                x=plot_us["date"], open=plot_us["open"], high=plot_us["high"],
                low=plot_us["low"], close=plot_us["close"], name="일봉",
                increasing_line_color=UP_COLOR, decreasing_line_color=DOWN_COLOR,
                increasing_fillcolor=UP_COLOR, decreasing_fillcolor=DOWN_COLOR,
            ), row=1, col=1)
        for _cn, _lb, _cl in [("ma5", "MA5", "#7b1fa2"), ("ma20", "MA20", "#ef6c00"), ("ma60", "MA60", "#2e7d32")]:
            fig_us.add_trace(
                go.Scatter(x=plot_us["date"], y=plot_us[_cn], name=_lb,
                           line=dict(width=1.5, color=_cl), mode="lines"), row=1, col=1)
        _vc = [UP_COLOR if c >= o else DOWN_COLOR for o, c in zip(plot_us["open"], plot_us["close"])]
        fig_us.add_trace(
            go.Bar(x=plot_us["date"], y=plot_us["volume"], name="거래량",
                   marker_color=_vc, opacity=0.6), row=2, col=1)
        fig_us.add_trace(
            go.Scatter(x=plot_us["date"], y=plot_us["vol_ma20"], name="거래량 MA20",
                       line=dict(width=1.2, color="#5e35b1"), mode="lines"), row=2, col=1)
        fig_us.add_trace(
            go.Scatter(x=plot_us["date"], y=plot_us["rsi"], name="RSI",
                       line=dict(width=1.5, color="#5e35b1"), mode="lines"), row=3, col=1)
        fig_us.add_hline(y=70, line_dash="dot", line_color=UP_COLOR, line_width=1, row=3, col=1)
        fig_us.add_hline(y=30, line_dash="dot", line_color=DOWN_COLOR, line_width=1, row=3, col=1)
        fig_us.update_layout(
            height=760, xaxis_rangeslider_visible=False,
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
            margin=dict(t=60, b=20, l=10, r=10),
            hovermode="x unified",
        )
        fig_us.update_yaxes(gridcolor="rgba(128,128,128,0.15)")
        fig_us.update_xaxes(gridcolor="rgba(128,128,128,0.1)")
        st.plotly_chart(fig_us, use_container_width=True)

        render_chart_tutor(df_us, unit="달러")

        _tr = r.get("tech")
        if _tr and not getattr(_tr, "error", None):
            usc1, usc2 = st.columns([1, 2])
            with usc1:
                st.markdown("**카테고리 점수** (합의 배수 ×{:.2f})".format(_tr.confluence_mult))
                us_cat_df = pd.DataFrame(
                    [{"카테고리": k, "가중치": technical_agent.CATEGORY_WEIGHTS[k], "점수": v}
                     for k, v in _tr.cat_scores.items()]
                )
                st.dataframe(us_cat_df, hide_index=True, use_container_width=True,
                             column_config={"점수": st.column_config.NumberColumn(format="%+.2f")})
            with usc2:
                st.markdown("**개별 신호와 증거**")
                us_sig_df = pd.DataFrame(
                    [{"카테고리": s.category, "신호": s.name, "점수": s.score, "증거": s.evidence}
                     for s in _tr.signals]
                )
                st.dataframe(us_sig_df, hide_index=True, use_container_width=True,
                             column_config={"점수": st.column_config.NumberColumn(format="%+.2f")})
            for _fl in getattr(_tr, "flags", []):
                st.warning(_fl)

        # ── 멀티기간 백테스트 ─────────────────────────────────────────
        st.markdown("### 🧪 멀티기간 백테스트 (기술점수 ≥ 0.30 신호)")
        us_bt = []
        for p in r["periods"]:
            if "note" in p:
                us_bt.append({"기간": p["label"], "신호수": p["n_signals"], "승률": "—",
                              "평균수익률": "—", "초과수익": "—", "샤프": "—"})
            else:
                us_bt.append({
                    "기간": p["label"], "신호수": p["n_signals"],
                    "승률": f"{p['win_rate']:.0%}",
                    "평균수익률": f"{p['avg_return']:+.1%}",
                    "초과수익": f"{p['excess']:+.1%}" if p.get("excess") is not None else "—",
                    "샤프": f"{p['sharpe']:.2f}" if p.get("sharpe") is not None else "—",
                })
        st.dataframe(pd.DataFrame(us_bt), hide_index=True, use_container_width=True)

        if info["summary"]:
            with st.expander("🏢 사업 요약 (영문)"):
                st.write(info["summary"])
    else:
        st.info("왼쪽 사이드바에 티커를 입력하고 **분석 실행**을 누르거나, 스크리너에서 → 버튼으로 이동하세요.")

elif ss.market == "US" and mode == MODE_SCREEN:
    st.title("🏆 오늘의 후보 종목 — S&P500")
    st.caption(
        "S&P500 전 종목을 yfinance로 일괄 수신 → 거래대금($) 상위 필터 → 기술점수(MA·RSI·MACD·볼린저·거래량) 랭킹. "
        "점수 엔진은 K-Trading과 동일합니다."
    )

    if us_scan_btn:
        ss.us_screener = None
        with st.status("🇺🇸 S&P500 스캔 중...", expanded=True) as status:
            bar = st.progress(0.0, text="구성종목 목록 로드 중...")
            try:
                result = us_screener.run(
                    liquidity_top=int(us_liq), n_top=int(us_top),
                    progress=lambda i, t, name: bar.progress(i / t, text=f"다운로드 {i}/{t} — {name}"),
                )
                ss.us_screener = result
                _global_store()["us_screener"] = result
                status.update(
                    label=f"✅ 완료 — {result['n_data']}종목 수신, 거래대금 상위 {result['n_liq']}종목 채점",
                    state="complete", expanded=False,
                )
            except Exception as e:
                st.error(f"스캔 오류: {e}")
                ss.us_screener = None

    usr = ss.us_screener
    if usr:
        st.markdown(
            f"**{usr['run_date']} 기준** · S&P500 {usr['n_universe']}종목 중 "
            f"{usr['n_data']}종목 수신 → 거래대금 상위 {usr['n_liq']}종목 → 기술점수 상위 {len(usr['rows'])}종목"
        )
        _w = [0.5, 0.9, 2.2, 1.1, 0.9, 1.0, 1.1, 0.5]
        _h = st.columns(_w)
        for _c, _t in zip(_h, ["순위", "티커", "종목", "섹터", "점수", "종가", "거래대금", "분석"]):
            _c.markdown(f"**{_t}**")
        for i, r in enumerate(usr["rows"], 1):
            _c = st.columns(_w)
            _c[0].write(i)
            _c[1].write(f"`{r['code']}`")
            _c[2].write(r["name"])
            _c[3].write(us_data.sector_kr(r["sector"]))
            _c[4].write(f"{r['score']:+.2f}")
            _c[5].write(f"${r['close']:,.2f}")
            _c[6].write(f"${r['value'] / 1e9:,.1f}B")
            _c[7].button("→", key=f"us_go_{r['code']}", on_click=goto_us_detail, args=(r["code"],))

        for r in usr["rows"]:
            with st.expander(f"📊 {r['code']} {r['name']} — 멀티기간 백테스트 ({r['n_days']}거래일)"):
                bt_rows = []
                for p in r["periods"]:
                    if "note" in p:
                        bt_rows.append({"기간": p["label"], "신호수": p["n_signals"],
                                        "승률": "—", "평균수익률": "—", "초과수익": "—", "샤프": "—"})
                    else:
                        bt_rows.append({
                            "기간": p["label"], "신호수": p["n_signals"],
                            "승률": f"{p['win_rate']:.0%}",
                            "평균수익률": f"{p['avg_return']:+.1%}",
                            "초과수익": f"{p['excess']:+.1%}" if p.get("excess") is not None else "—",
                            "샤프": f"{p['sharpe']:.2f}" if p.get("sharpe") is not None else "—",
                        })
                st.dataframe(pd.DataFrame(bt_rows), hide_index=True, use_container_width=True)
        st.caption("백테스트: 기술점수 ≥ 0.30 신호의 보유기간별 성과 (같은 종목 과거 데이터 기준, 참고용)")
    else:
        st.info("왼쪽 사이드바에서 **S&P500 스캔** 버튼을 눌러주세요.")

# ── 모드: 종목 분석 ─────────────────────────────────────────────────────
elif mode == MODE_DETAIL:
    if ss.get("nav_from"):
        st.button(f"← {ss['nav_from']} 결과로 돌아가기", on_click=nav_go_back)
    st.title("한국 주식 멀티 에이전트 분석")
    st.caption("Agent 1 (뉴스/공시) → Agent 2 (기술 4축) → Agent 3 (펀더멘탈) → Agent 4 (전략 종합)")

    target, force = None, False
    if run_btn:
        _q = code_in.strip()
        if re.fullmatch(r"\d{4,6}", _q):
            target, force = _q, True
            ss.kr_candidates = None
        else:
            # 이름 검색 → 단일이면 바로 분석, 복수면 후보 선택
            _cands = stock_search.search(_q, market="KR")
            if len(_cands) == 1:
                target, force = _cands[0]["code"], True
                ss.kr_candidates = None
            elif _cands:
                ss.kr_candidates = {"query": _q, "items": _cands}
            else:
                st.warning(f"'{_q}' 검색 결과가 없습니다. 이름 또는 6자리 코드를 확인해 주세요.")
                ss.kr_candidates = None
    pending = ss.pop("pending", None)
    if pending:
        target, force = pending, False

    _kc = ss.get("kr_candidates")
    if _kc and not target:
        st.markdown(f"**'{_kc['query']}' 검색 결과** — 분석할 종목을 선택하세요:")
        for c in _kc["items"]:
            st.button(f"🔍 {c['name']}  ·  {c['code']}  ·  {c['exchange']}",
                      key=f"krs_{c['code']}", on_click=goto_detail, args=(c["code"],))

    if target:
        code6 = re.sub(r"\D", "", target).zfill(6)
        if force or code6 not in ss.results:
            ss.results[code6] = analyze(code6)
        ss.last_analyzed = code6

    if ss.last_analyzed and ss.last_analyzed in ss.results:
        cached = ss.results[ss.last_analyzed]
        # cached = (news, tech, fund, strat, comm) — v4 튜플
        if len(cached) == 5:
            news, tech, fund, strat, comm = cached
        else:
            # 구버전 캐시 (news, tech, strat, comm) 호환
            news, tech, strat = cached[:3]
            fund = None
            comm = cached[3] if len(cached) > 3 else None
        if strat.error:
            st.error(strat.error)
        else:
            render_analysis(news, tech, fund, strat, comm)
    else:
        st.info("왼쪽 사이드바에서 종목코드를 입력하고 **에이전트 분석 실행** 버튼을 눌러주세요.")

# ── 모드: 오늘의 후보 종목 ──────────────────────────────────────────────
elif mode == MODE_SCREEN:
    st.title("🏆 오늘의 후보 종목")
    st.caption("1단계: 거래대금 상위 종목 근사 기술 점수 → 2단계: 상위 종목 뉴스+펀더멘탈 풀 분석 → 합산 점수 랭킹")

    if scan_btn:
        with st.status("전 시장 스캔 중...", expanded=True) as status:
            bar1 = st.progress(0.0, text="1단계 준비 중...")
            cands = screener.stage1(
                n_liq,
                progress=lambda i, t, l: bar1.progress(i / t, text=f"1단계 기술 필터 {i}/{t} — {l}"),
            )
            st.write(f"→ 1단계 완료: {len(cands)}종목 점수 계산, 상위 {n_full}종목 풀 분석 진행")
            bar2 = st.progress(0.0, text="2단계 준비 중...")
            final = screener.stage2(
                cands, n_full,
                progress=lambda i, t, l: bar2.progress(i / t, text=f"2단계 풀 분석 {i}/{t} — {l}"),
            )
            status.update(label=f"스캔 완료 ✅ — 후보 {len(final)}종목", state="complete", expanded=False)
        try:
            top_search = community.top_searched()
        except Exception:
            top_search = []
        ss.screener = {"final": final, "params": (n_liq, n_full), "top_search": top_search}
        _global_store()["screener"] = ss.screener
        for c in final:
            ss.results[c.code] = (c.news, c.tech, c.fund, c.strat, c.comm)

    sc = ss.screener
    if not sc:
        st.info("왼쪽 사이드바에서 **오늘의 후보 스캔** 버튼을 눌러주세요. "
                "1단계는 종목당 요청 1번이라 1~2분 내에 끝납니다.")
    else:
        final = sc["final"]
        st.markdown(f"**풀 분석 {len(final)}종목 — 합산 점수 순** "
                    f"(1단계 {sc['params'][0]}종목 → 2단계 {sc['params'][1]}종목)")

        widths = [0.4, 1.8, 1.0, 0.9, 0.9, 1.1, 1.4, 1.3, 0.65, 0.65, 0.65]
        hdr = st.columns(widths)
        for col, label in zip(hdr, ["#", "종목", "현재가", "합산", "기술/펀더", "등급/의견",
                                     "커뮤니티", "매수가/손절", "상세", "📊", "💼"]):
            col.markdown(f"**{label}**")

        for i, c in enumerate(final, 1):
            s = c.strat
            color = score_color(s.combined_score)
            buzz = ""
            if c.comm is not None:
                parts = []
                if c.comm.search_rank:
                    parts.append(f"🔥{c.comm.search_rank}위")
                if c.comm.board_ratio is None and c.comm.board_today:
                    parts.append("토론급증")
                elif c.comm.board_ratio and c.comm.board_ratio >= 2:
                    parts.append(f"×{c.comm.board_ratio}")
                buzz = " · ".join(parts) or "-"
            cols = st.columns(widths)
            cols[0].markdown(f"<div class='rank-row'>{i}</div>", unsafe_allow_html=True)
            cols[1].markdown(f"<div class='rank-row'><b>{c.name}</b> <span style='color:gray; font-size:0.8rem'>{c.code}</span></div>",
                             unsafe_allow_html=True)
            cols[2].markdown(f"<div class='rank-row'>{c.tech.price:,.0f}</div>", unsafe_allow_html=True)
            cols[3].markdown(
                f"<div class='rank-row' style='color:{color}; font-weight:800; font-size:1.05rem'>{s.combined_score}</div>",
                unsafe_allow_html=True,
            )
            cols[4].markdown(
                f"<div class='rank-row' style='font-size:0.82rem;'>{s.score100} / {s.fund_score}</div>",
                unsafe_allow_html=True,
            )
            cols[5].markdown(f"<div class='rank-row'>{s.grade} <span style='color:gray; font-size:0.82rem'>({s.opinion})</span></div>",
                             unsafe_allow_html=True)
            cols[6].markdown(f"<div class='rank-row' style='font-size:0.82rem'>{buzz}</div>", unsafe_allow_html=True)
            cols[7].markdown(f"<div class='rank-row' style='font-size:0.82rem'>{s.buy_price:,.0f} / {s.stop_loss:,.0f}</div>",
                             unsafe_allow_html=True)
            cols[8].button("→", key=f"detail_{c.code}", on_click=goto_detail, args=(c.code,))
            if cols[9].button("📊", key=f"bt_{c.code}", help="멀티기간 백테스트"):
                ss.bt_open = None if ss.bt_open == c.code else c.code
                ss.pf_quick_add = None
            if cols[10].button("💼", key=f"pf_{c.code}", help="포트폴리오에 추가"):
                ss.pf_quick_add = None if ss.pf_quick_add == c.code else c.code
                ss.bt_open = None

        st.caption("합산 점수 = 기술 60% + 펀더멘탈 40%. 📊 백테스트 · 💼 포트폴리오 추가")

        # ── 백테스트 패널 ────────────────────────────────────────────────
        if ss.bt_open:
            bt_code = ss.bt_open
            bt_name = next((c.name for c in final if c.code == bt_code), bt_code)
            bt_price = next((c.tech.price for c in final if c.code == bt_code), 0)
            st.divider()
            st.markdown(f"### 📊 백테스트 — {bt_name} ({bt_code})")
            st.caption("기술점수 ≥ 0.30 신호 발생일 종가 진입 → 각 기간 후 종가 청산 (무손절·무목표 순수 보유 시뮬레이션) | 데이터: 최근 2.5년치 일봉")

            if bt_code not in ss.bt_cache:
                with st.spinner(f"{bt_name} 2.5년 데이터 로드 & 시뮬레이션 중..."):
                    from agents import backtest as _bt
                    try:
                        ss.bt_cache[bt_code] = _bt.run_multiperiod_for_code(bt_code)
                    except Exception as e:
                        st.error(f"백테스트 오류: {e}")
                        ss.bt_cache[bt_code] = None

            bt_res = ss.bt_cache.get(bt_code)
            if bt_res:
                total_days = bt_res["total_days"]
                periods = bt_res["periods"]
                st.caption(f"사용 데이터: {total_days}거래일 | 신호 기준: 기술점수 ≥ {bt_res['threshold']}")

                # 통계 테이블
                rows = []
                for p in periods:
                    if "note" in p:
                        rows.append({"기간": p["label"], "신호수": p["n_signals"],
                                     "승률": "—", "평균수익률": "—", "초과수익": "—",
                                     "샤프": "—", "평균상승": "—", "평균하락": "—",
                                     "기대값": "—"})
                    else:
                        rows.append({
                            "기간": p["label"],
                            "신호수": p["n_signals"],
                            "승률": f"{p['win_rate']:.0%}",
                            "평균수익률": f"{p['avg_return']:+.1%}",
                            "초과수익": f"{p['excess']:+.1%}" if p.get("excess") is not None else "—",
                            "샤프": f"{p['sharpe']:.2f}" if p.get("sharpe") is not None else "—",
                            "평균상승": f"{p['avg_win']:+.1%}",
                            "평균하락": f"{p['avg_loss']:+.1%}",
                            "기대값": f"{p['expectancy']:+.1%}",
                        })
                st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

                # plotly 차트
                valid_p = [p for p in periods if "note" not in p]
                if valid_p:
                    labels      = [p["label"] for p in valid_p]
                    avg_rets    = [p["avg_return"] * 100 for p in valid_p]
                    benchmarks  = [p["benchmark"] * 100 if p.get("benchmark") is not None else 0 for p in valid_p]
                    bar_colors  = ["#d32f2f" if v >= 0 else "#1565c0" for v in avg_rets]
                    fig = go.Figure()
                    fig.add_bar(name="신호 평균수익률", x=labels, y=avg_rets,
                                marker_color=bar_colors, opacity=0.85)
                    fig.add_bar(name="무신호 벤치마크", x=labels, y=benchmarks,
                                marker_color="rgba(128,128,128,0.4)")
                    fig.add_hline(y=0, line_dash="dash", line_color="gray", line_width=1)
                    fig.update_layout(
                        barmode="group", height=300,
                        margin=dict(l=0, r=0, t=20, b=0),
                        yaxis_title="수익률 (%)",
                        legend=dict(orientation="h", y=1.1),
                        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                    )
                    fig.update_yaxes(ticksuffix="%", gridcolor="rgba(128,128,128,0.15)")
                    st.plotly_chart(fig, use_container_width=True)

                with st.expander("기간별 최대상승 / 최대하락 / 변동성"):
                    risk_rows = []
                    for p in periods:
                        if "note" not in p:
                            risk_rows.append({
                                "기간": p["label"],
                                "최대 상승": f"{p['max_gain']:+.1%}",
                                "최대 하락": f"{p['max_loss']:+.1%}",
                                "변동성(σ)": f"{p['std']:.1%}",
                            })
                    if risk_rows:
                        st.dataframe(pd.DataFrame(risk_rows), hide_index=True, use_container_width=True)

        # ── 포트폴리오 빠른 추가 패널 ────────────────────────────────────
        if ss.pf_quick_add:
            qa_code  = ss.pf_quick_add
            qa_name  = next((c.name for c in final if c.code == qa_code), qa_code)
            qa_price = next((c.tech.price for c in final if c.code == qa_code), 0)
            st.divider()
            st.markdown(f"### 💼 포트폴리오 추가 — {qa_name} ({qa_code})")
            try:
                pf_list = portfolio_agent.list_portfolios()
            except Exception:
                pf_list = [portfolio_agent.DEFAULT_PORTFOLIO]
            with st.form("quick_add_form", clear_on_submit=True):
                qa1, qa2, qa3, qa4 = st.columns([2, 1.5, 1, 1.5])
                sel_pf  = qa1.selectbox("계좌", pf_list, key="qa_portfolio")
                buy_px  = qa2.number_input("매수가 (원)", min_value=1,
                                           value=int(qa_price) if qa_price else 70000,
                                           step=100, key="qa_price")
                qty     = qa3.number_input("수량", min_value=1, value=10, step=1, key="qa_qty")
                buy_dt  = qa4.text_input("매수일", placeholder="2025-01-15", key="qa_date")
                if st.form_submit_button("✅ 추가", type="primary", use_container_width=True):
                    try:
                        portfolio_agent.add_position(
                            portfolio_agent.Position(
                                code=qa_code, name=qa_name,
                                buy_price=float(buy_px), quantity=int(qty),
                                buy_date=buy_dt,
                            ),
                            portfolio=sel_pf,
                        )
                        st.success(f"{qa_name}을(를) [{sel_pf}]에 추가했습니다.")
                        ss.pf_quick_add = None
                        st.rerun()
                    except Exception as e:
                        st.error(f"추가 실패: {e}")

        if sc.get("top_search"):
            with st.expander("🔥 네이버 검색상위 30 — 지금 커뮤니티가 보고 있는 종목 (참고)"):
                ts_df = pd.DataFrame(
                    [{"순위": t.rank, "종목명": t.name, "코드": t.code, "검색비율": t.ratio}
                     for t in sc["top_search"]]
                )
                st.dataframe(ts_df, hide_index=True, use_container_width=True)
                st.caption("검색 관심은 급등주와 급락주 모두에서 치솟습니다 — 방향이 아니라 '주목도'의 지표입니다.")

# ── 모드: 포트폴리오 관리 ───────────────────────────────────────────────
elif mode == MODE_PORTFOLIO:
    _SIG_COLOR = {
        "손절": "#d32f2f", "익절": "#1565c0",
        "익절 고려": "#f57c00", "보유": "#2e7d32",
        "오류": "gray", "확인중": "gray",
    }
    ss.setdefault("pf_selected", None)

    # ── 계좌 목록 로드 ──────────────────────────────────────────────────
    try:
        portfolios = portfolio_agent.list_portfolios()
    except Exception as e:
        st.error(f"Google Sheets 연결 오류: {e}")
        portfolios = []
        st.stop()

    # 선택된 계좌가 목록에 없으면 첫 번째로 초기화
    if ss.pf_selected not in portfolios:
        ss.pf_selected = portfolios[0] if portfolios else portfolio_agent.DEFAULT_PORTFOLIO

    # ── 계좌 선택 / 관리 바 ─────────────────────────────────────────────
    st.title("💼 내 포트폴리오")

    # ── ✨ 추천 포트폴리오 생성기 (모의투자 시작점) ──────────────────────
    ss.setdefault("reco", None)
    _rmkt = ss.market
    _runit = "$" if _rmkt == "US" else "원"
    with st.expander(f"✨ 추천 포트폴리오 만들기 — {'🇺🇸 S&P500' if _rmkt == 'US' else '🇰🇷 국장'} 스크리너 기반"):
        st.caption(
            "스크리너 기술점수 상위 종목으로 비중·수량까지 계산해 드립니다. "
            "모의투자 계좌로 저장하면 손절선·목표가·신호가 자동 추적되고, 같은 표로 실전 매수도 따라할 수 있어요."
        )
        rc1, rc2, rc3 = st.columns(3)
        reco_n = rc1.slider("종목 수", 5, 15, 10, key="reco_n")
        reco_w_label = rc2.selectbox("비중 방식", list(fund_agent.WEIGHT_LABELS.values()), key="reco_w")
        _cap_default = 10_000.0 if _rmkt == "US" else 10_000_000.0
        reco_cap = rc3.number_input(
            f"투자금 ({_runit})", min_value=100.0, value=_cap_default,
            step=1_000.0 if _rmkt == "US" else 1_000_000.0, key=f"reco_cap_{_rmkt}",
        )
        if st.button("🔮 추천 포트폴리오 생성", type="primary", key="reco_btn"):
            with st.status("스크리너 실행 중... (30~60초)", expanded=True) as _rs:
                _rbar = st.progress(0.0)
                try:
                    ss.reco = recommend_agent.build(
                        market=_rmkt, n_stocks=int(reco_n),
                        weighting={v: k for k, v in fund_agent.WEIGHT_LABELS.items()}[reco_w_label],
                        capital=float(reco_cap),
                        progress=lambda i, t, n: _rbar.progress(i / t, text=f"{i}/{t} — {n}"),
                    )
                    _rs.update(label="✅ 추천 생성 완료", state="complete", expanded=False)
                except Exception as e:
                    st.error(f"생성 실패: {e}")
                    ss.reco = None

        _reco = ss.get("reco")
        if _reco and _reco["market"] == _rmkt:
            _rfmt = (lambda v: f"${v:,.2f}") if _rmkt == "US" else (lambda v: f"{v:,.0f}원")
            reco_tbl = [{
                "종목":   f"{r['name']}({r['code']})",
                "섹터":   us_data.sector_kr(r["sector"]) if r["sector"] else "—",
                "점수":   f"{r['score']:+.2f}",
                "비중":   f"{r['weight']:.0%}",
                "현재가": _rfmt(r["price"]),
                "수량":   r["qty"],
                "금액":   _rfmt(r["amount"]),
            } for r in _reco["rows"]]
            st.dataframe(pd.DataFrame(reco_tbl), hide_index=True, use_container_width=True)
            st.caption(
                f"{_reco['run_date']} 기준 · 투자 {_rfmt(_reco['invested'])} + 현금 {_rfmt(_reco['cash'])} · "
                f"{fund_agent.WEIGHT_LABELS.get(_reco['weighting'], _reco['weighting'])} · "
                "⚠️ 기술점수 기반 후보이며 투자 권유가 아닙니다."
            )
            _rname_default = f"모의-{'US' if _rmkt == 'US' else 'K'}-{_reco['run_date'][5:].replace('-', '')}"
            sv1, sv2 = st.columns([2, 1.4])
            reco_name = sv1.text_input("모의투자 계좌명", value=_rname_default, key="reco_name")
            sv2.markdown("<div style='height:1.7rem'></div>", unsafe_allow_html=True)
            if sv2.button("📝 모의투자 계좌로 저장", type="primary", key="reco_save"):
                try:
                    portfolio_agent.add_portfolio(reco_name)
                    _n_saved = 0
                    for r in _reco["rows"]:
                        if r["qty"] > 0:
                            # 실전 재현성: 수수료+슬리피지 0.3% 가산한 체결가로 기록
                            _bp = r["price"] * 1.003
                            _bp = round(_bp, 2) if _reco["market"] == "US" else round(_bp)
                            portfolio_agent.add_position(
                                portfolio_agent.Position(
                                    code=r["code"], name=r["name"],
                                    buy_price=_bp, quantity=int(r["qty"]),
                                    buy_date=_reco["run_date"], market=_reco["market"],
                                ),
                                portfolio=reco_name,
                            )
                            _n_saved += 1
                    ss.pf_selected = reco_name
                    st.success(f"✅ '{reco_name}' 계좌에 {_n_saved}종목 저장 — "
                               f"종가 + 0.3%(수수료·슬리피지)가 매수가로 기록됐습니다.")
                    st.rerun()
                except Exception as e:
                    st.error(f"저장 실패: {e}")
    bar_l, bar_m, bar_r = st.columns([3, 1.5, 1.5])
    with bar_l:
        selected_pf = st.selectbox(
            "계좌 선택", portfolios,
            index=portfolios.index(ss.pf_selected) if ss.pf_selected in portfolios else 0,
            key="pf_select_box",
            label_visibility="collapsed",
        )
        ss.pf_selected = selected_pf

    with bar_m:
        with st.popover("➕ 계좌 추가"):
            new_pf_name = st.text_input("새 계좌 이름", key="new_pf_name", placeholder="B계좌")
            if st.button("만들기", key="btn_add_pf"):
                n = ss.get("new_pf_name", "").strip()
                if n and n not in portfolios:
                    try:
                        portfolio_agent.add_portfolio(n)
                        ss.pf_selected = n
                        st.rerun()
                    except Exception as e:
                        st.error(str(e))
                elif n in portfolios:
                    st.warning("같은 이름의 계좌가 이미 있습니다.")
                else:
                    st.warning("이름을 입력해주세요.")

    with bar_r:
        with st.popover("⚙️ 계좌 관리"):
            rename_val = st.text_input("이름 변경", value=selected_pf, key="rename_pf_val")
            if st.button("이름 변경", key="btn_rename_pf"):
                new_n = ss.get("rename_pf_val", "").strip()
                if new_n and new_n != selected_pf:
                    try:
                        portfolio_agent.rename_portfolio(selected_pf, new_n)
                        ss.pf_selected = new_n
                        st.rerun()
                    except Exception as e:
                        st.error(str(e))
            st.divider()
            st.caption(f"**{selected_pf}** 계좌를 삭제합니다.")
            if st.button("🗑️ 계좌 삭제", key="btn_del_pf", type="secondary"):
                try:
                    portfolio_agent.delete_portfolio(selected_pf)
                    ss.pf_selected = None
                    st.rerun()
                except Exception as e:
                    st.error(str(e))

    st.caption(f"손절선 = 매수가 − 1.5×ATR  |  1차 목표 = 매수가 + 2.0×ATR  |  2차 목표 = 매수가 + 3.0×ATR")
    st.divider()

    # ── 포지션 로드 ─────────────────────────────────────────────────────
    try:
        positions = portfolio_agent.load_positions(selected_pf)
    except Exception as e:
        st.error(f"포지션 로드 오류: {e}")
        positions = []

    # ── 포지션 추가 폼 ─────────────────────────────────────────────────
    with st.expander("➕ 포지션 추가", expanded=len(positions) == 0):
        with st.form("add_pos_form", clear_on_submit=True):
            fc1, fc2, fc3, fc4, fc5 = st.columns([1.2, 2, 1.5, 1.2, 1.5])
            fc1.text_input("종목코드/티커", placeholder="005930 또는 AAPL", key="pf_code")
            fc2.text_input("종목명", placeholder="삼성전자", key="pf_name")
            fc3.number_input("매수가", min_value=0.01, value=70000.0, step=100.0, key="pf_price")
            fc4.number_input("수량 (주)", min_value=1, value=10, step=1, key="pf_qty")
            fc5.text_input("매수일", placeholder="2025-01-15", key="pf_date")
            submitted = st.form_submit_button("추가", type="primary", use_container_width=True)
            if submitted:
                _raw = ss.get("pf_code", "").strip()
                if re.fullmatch(r"\d{4,6}", _raw):
                    _pcode, _pmkt = _raw.zfill(6), "KR"
                elif re.fullmatch(r"[A-Za-z.\-]{1,6}", _raw):
                    _pcode, _pmkt = _raw.upper(), "US"
                else:
                    _pcode = None
                if _pcode:
                    try:
                        portfolio_agent.add_position(
                            portfolio_agent.Position(
                                code=_pcode,
                                name=ss.get("pf_name", _pcode),
                                buy_price=float(ss.get("pf_price", 0)),
                                quantity=int(ss.get("pf_qty", 0)),
                                buy_date=ss.get("pf_date", ""),
                                market=_pmkt,
                            ),
                            portfolio=selected_pf,
                        )
                        st.success(f"✅ {ss.get('pf_name', _pcode)} 추가됐습니다.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"추가 실패: {e}")
                else:
                    st.warning("국장은 6자리 코드(005930), 미장은 티커(AAPL)를 입력해주세요.")

    if not positions:
        st.info(f"**{selected_pf}**에 포지션이 없습니다. 위 폼에서 추가해주세요.")
    else:
        # ── 신호 계산 ──────────────────────────────────────────────────
        with st.spinner("시세·ATR·전략점수 계산 중..."):
            signals = portfolio_agent.calc_all_signals(positions)

        # 계좌 통화: 미장 포지션 포함 시 $ 표기
        _pf_us  = any(s.market == "US" for s in signals)
        _pfmt   = (lambda v: f"${v:,.2f}") if _pf_us else (lambda v: f"{v:,.0f}원")
        _pfmt_s = (lambda v: f"${v:+,.2f}") if _pf_us else (lambda v: f"{v:+,.0f}원")
        _pnum   = (lambda v: f"{v:,.2f}") if _pf_us else (lambda v: f"{v:,.0f}")

        # ── 요약 메트릭 ────────────────────────────────────────────────
        total_invest = sum(s.buy_price * s.quantity for s in signals)
        total_eval   = sum(s.eval_amount for s in signals)
        total_pl     = total_eval - total_invest
        total_pl_pct = total_pl / total_invest * 100 if total_invest else 0

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("포지션 수", f"{len(signals)}개")
        m2.metric("총 매수금액", _pfmt(total_invest))
        m3.metric("총 평가금액", _pfmt(total_eval))
        m4.metric("총 손익", _pfmt_s(total_pl), f"{total_pl_pct:+.1f}%",
                  delta_color="normal" if total_pl >= 0 else "inverse")

        if _pf_us:
            _fx = us_data.usd_krw()
            _div_total = sum(s.dividends for s in signals)
            _fx_txt = (f"환율 {_fx:,.0f}원 기준 총평가 ≈ ₩{total_eval * _fx:,.0f}" if _fx else "환율 조회 실패")
            _dv_txt = f" · 보유기간 배당 수령 ${_div_total:,.2f}" if _div_total > 0 else ""
            st.caption(f"💱 {_fx_txt}{_dv_txt} (배당은 수익률에 미포함, 별도 표시)")

        st.divider()

        # ── 신호 테이블 ────────────────────────────────────────────────
        col_w = [1.2, 2.0, 1.3, 1.3, 1.3, 1.1, 1.1, 1.6, 1.2, 1.8]
        hdr = st.columns(col_w)
        for col, label in zip(hdr, ["코드", "종목명", "매수가", "현재가", "수익률",
                                     "손절선", "목표1", "목표2", "기술점수", "신호"]):
            col.markdown(f"**{label}**")

        for sig in signals:
            color    = _SIG_COLOR.get(sig.signal, "gray")
            rp_color = "#d32f2f" if sig.return_pct >= 0 else "#1565c0"
            sc_color = "#2e7d32" if sig.strategy_score >= 30 else ("#f57c00" if sig.strategy_score >= 0 else "#d32f2f")
            row = st.columns(col_w)
            row[0].markdown(f"`{sig.code}`")
            row[1].markdown(f"**{sig.name}**")
            row[2].markdown(_pnum(sig.buy_price))
            row[3].markdown(_pnum(sig.current_price) if sig.current_price else "—")
            row[4].markdown(
                f"<span style='color:{rp_color}; font-weight:700'>{sig.return_pct:+.1f}%</span>",
                unsafe_allow_html=True,
            )
            row[5].markdown(_pnum(sig.stop_loss) if sig.stop_loss else "—")
            row[6].markdown(_pnum(sig.target1) if sig.target1 else "—")
            row[7].markdown(_pnum(sig.target2) if sig.target2 else "—")
            row[8].markdown(
                f"<span style='color:{sc_color}; font-weight:700'>{sig.strategy_score:+d}</span>",
                unsafe_allow_html=True,
            )
            row[9].markdown(
                f"<span style='color:{color}; font-weight:800'>{sig.signal}</span>",
                unsafe_allow_html=True,
            )

        # ── 상세 설명 (expander) ────────────────────────────────────────
        st.divider()
        st.markdown("**신호 상세**")
        for sig in signals:
            color    = _SIG_COLOR.get(sig.signal, "gray")
            sc_color = "#2e7d32" if sig.strategy_score >= 30 else ("#f57c00" if sig.strategy_score >= 0 else "#d32f2f")
            with st.expander(f"{sig.signal} — {sig.name}({sig.code})"):
                if sig.error:
                    st.error(sig.error)
                else:
                    d1, d2, d3 = st.columns(3)
                    d1.metric("평가금액", _pfmt(sig.eval_amount))
                    d2.metric("손익", _pfmt_s(sig.profit_loss), f"{sig.return_pct:+.1f}%",
                              delta_color="normal" if sig.profit_loss >= 0 else "inverse")
                    d3.metric("ATR(14)", _pfmt(sig.atr))
                    st.markdown(
                        f"**신호 이유**: <span style='color:{color}'>{sig.signal_reason}</span>",
                        unsafe_allow_html=True,
                    )
                    # 기술점수 게이지 (-100~+100 → 0~100 표시)
                    gauge_val = (sig.strategy_score + 100) / 200
                    st.markdown(
                        f"<span style='color:{sc_color}'>기술점수 {sig.strategy_score:+d}</span>"
                        f" <span style='color:gray; font-size:0.85rem'>(-100=강한하락, 0=중립, +100=강한상승)</span>",
                        unsafe_allow_html=True,
                    )
                    st.progress(gauge_val)
                if st.button(f"🗑️ {sig.name} 삭제", key=f"del_{sig.code}"):
                    try:
                        portfolio_agent.remove_position(sig.code, portfolio=selected_pf)
                        st.success(f"✅ {sig.name} 삭제됨")
                        st.rerun()
                    except Exception as e:
                        st.error(f"삭제 실패: {e}")

# ── 모드: 백데이터 검증 ─────────────────────────────────────────────────
elif mode == MODE_BACKDATA:
    from datetime import date, timedelta
    st.title("📅 백데이터 검증")
    st.caption(
        "과거 특정 날짜에 이 시스템으로 스크리닝했을 때 나왔을 종목과, 그 이후 실제 수익률을 확인합니다. "
        "기술점수(MA·RSI·MACD·볼린저·거래량) 기준으로 탐색합니다."
    )

    if bd_btn:
        ss.bd_result = None   # 캐시 초기화 후 재실행
        with st.status(f"📅 {bd_date} 기준 백데이터 시뮬레이션...", expanded=True) as status:
            bar = st.progress(0.0, text="종목 스캔 준비 중...")
            try:
                result = backdata_agent.run(
                    target_date_str=str(bd_date),
                    n_universe=bd_universe,
                    n_top=bd_top,
                    include_dart=bd_dart,
                    progress=lambda i, t, name: bar.progress(
                        i / t, text=f"{i}/{t} — {name}"
                    ),
                )
                ss.bd_result = result
                _global_store()["bd_result"] = result
                status.update(
                    label=f"✅ 완료 — {result['n_scanned']}종목 스캔, 상위 {len(result['top'])}종목",
                    state="complete", expanded=False,
                )
            except Exception as e:
                st.error(f"시뮬레이션 오류: {e}")
                ss.bd_result = None

    res = ss.bd_result
    if res:
        st.markdown(
            f"**{res['target_date']} 기준** · "
            f"탐색 {res['n_scanned']}종목 중 기술점수 상위 {len(res['top'])}종목"
        )

        # ── 요약 통계 ──────────────────────────────────────────────────
        st.markdown("#### 기간별 요약 (상위 종목 단순 평균)")
        labels  = res["period_labels"]
        summary = res["summary"]
        bm      = res["benchmark"]

        sum_rows = []
        for label in labels:
            s = summary.get(label)
            sum_rows.append({
                "기간":          label,
                "신호 평균수익률": f"{s['avg']:+.1%}" if s else "N/A",
                "초과수익":       f"{s['excess']:+.1%}" if s and s.get("excess") is not None else "N/A",
                "샤프":          f"{s['sharpe']:.2f}" if s and s.get("sharpe") is not None else "N/A",
                "승률":          f"{s['win_rate']:.0%}" if s else "N/A",
                "최고":          f"{s['max']:+.1%}" if s else "N/A",
                "최저":          f"{s['min']:+.1%}" if s else "N/A",
                "표본 수":        s["n"] if s else 0,
            })
        st.dataframe(pd.DataFrame(sum_rows), hide_index=True, use_container_width=True)

        # ── plotly 막대 차트 ──────────────────────────────────────────
        valid_sum = [
            (l, summary[l]["avg"] * 100, (bm.get(l) or 0) * 100)
            for l in labels if l in summary
        ]
        if valid_sum:
            _l, _avgs, _bms = zip(*valid_sum)
            bar_colors = ["#2e7d32" if v >= 0 else "#d32f2f" for v in _avgs]
            fig_bd = go.Figure()
            fig_bd.add_bar(name=f"신호 상위{len(res['top'])}종목 평균", x=list(_l), y=list(_avgs),
                           marker_color=bar_colors, opacity=0.85)
            fig_bd.add_bar(name="전체 종목 평균(벤치마크)", x=list(_l), y=list(_bms),
                           marker_color="rgba(128,128,128,0.4)")
            fig_bd.add_hline(y=0, line_dash="dash", line_color="gray", line_width=1)
            fig_bd.update_layout(
                barmode="group", height=300,
                margin=dict(l=0, r=0, t=20, b=0),
                yaxis_title="수익률 (%)",
                legend=dict(orientation="h", y=1.12),
                plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
            )
            fig_bd.update_yaxes(ticksuffix="%", gridcolor="rgba(128,128,128,0.15)")
            st.plotly_chart(fig_bd, use_container_width=True)

        # ── 종목별 수익률 테이블 ──────────────────────────────────────
        st.markdown("#### 종목별 기간별 실제 수익률")
        top = res["top"]
        tbl_rows = []
        for c in top:
            row = {
                "종목":    f"{c['name']}({c['code']})",
                "당시 종가": f"{c['entry_price']:,.0f}원",
                "기술점수":  f"{c['score']:+.2f}",
            }
            for label in labels:
                v = c["returns"].get(label)
                row[label] = f"{v:+.1%}" if v is not None else "N/A"
            tbl_rows.append(row)
        st.dataframe(pd.DataFrame(tbl_rows), hide_index=True, use_container_width=True)

        # ── DART 펀더멘탈 (선택 시) ───────────────────────────────────
        if res.get("include_dart") and res.get("dart_year"):
            st.markdown(f"#### 펀더멘탈 ({res['dart_year']}년 사업보고서 기준)")
            dart_rows = []
            for c in top:
                d = c.get("dart")
                dart_rows.append({
                    "종목":     c["name"],
                    "F-Score":  f"{d['f_score']}/9" if d else "N/A",
                    "ROA":      f"{d['roa']:+.1f}%" if d and d.get("roa") is not None else "N/A",
                    "부채비율": f"{d['debt_ratio']:.0f}%" if d and d.get("debt_ratio") is not None else "N/A",
                    "영업이익률": f"{d['op_margin']:+.1f}%" if d and d.get("op_margin") is not None else "N/A",
                    "매출성장": f"{d['revenue_growth']:+.1f}%" if d and d.get("revenue_growth") is not None else "N/A",
                    "영업이익성장": f"{d['op_profit_growth']:+.1f}%" if d and d.get("op_profit_growth") is not None else "N/A",
                })
            st.dataframe(pd.DataFrame(dart_rows), hide_index=True, use_container_width=True)
            # F-Score 상세 expander
            for c in top:
                d = c.get("dart")
                if d and d.get("f_details"):
                    with st.expander(f"F-Score 상세 — {c['name']} ({d['f_score']}/9)"):
                        for line in d["f_details"]:
                            st.caption(line)

        # ── plotly 히트맵 ─────────────────────────────────────────────
        st.markdown("#### 수익률 히트맵")
        z_vals, y_names, text_vals = [], [], []
        for c in top:
            row_z, row_t = [], []
            for label in labels:
                v = c["returns"].get(label)
                row_z.append(v * 100 if v is not None else None)
                row_t.append(f"{v:+.1%}" if v is not None else "N/A")
            z_vals.append(row_z)
            text_vals.append(row_t)
            y_names.append(c["name"])

        fig_heat = go.Figure(go.Heatmap(
            z=z_vals, x=labels, y=y_names,
            text=text_vals, texttemplate="%{text}",
            colorscale=[
                [0.0, "#1565c0"], [0.45, "#bbdefb"],
                [0.5, "#f5f5f5"],
                [0.55, "#ffcdd2"], [1.0, "#d32f2f"],
            ],
            zmid=0,
            colorbar=dict(title="수익률(%)", ticksuffix="%"),
            hovertemplate="<b>%{y}</b><br>%{x}: %{text}<extra></extra>",
        ))
        fig_heat.update_layout(
            height=max(250, len(top) * 36 + 80),
            margin=dict(l=0, r=0, t=20, b=0),
            yaxis=dict(autorange="reversed"),
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig_heat, use_container_width=True)
        st.caption(
            "히트맵: 빨강=수익, 파랑=손실, 흰색=보합 · "
            "N/A=아직 미도래 · ⚠️ 생존 편향(현재 상장 종목 기준)"
        )

    else:
        st.info("왼쪽 사이드바에서 날짜를 선택하고 **백데이터 시뮬레이션 실행** 버튼을 눌러주세요.")

elif mode == MODE_FUND:
    st.title("🏦 펀드 시뮬레이션")
    st.caption(
        "설정일부터 오늘까지, 리밸런싱 시점마다 스크리너를 새로 돌려(그 시점 거래대금 상위 유니버스 재구성 → "
        "기술점수 상위 편입) 운용했을 때의 펀드 성과입니다. 기준가 1,000원 시작, 거래비용 편도 0.3% 반영."
    )

    _weight_key = {v: k for k, v in fund_agent.WEIGHT_LABELS.items()}[fd_weight]
    _rebal_days = fund_agent.REBALANCE_OPTIONS[fd_rebal]

    if fd_btn:
        ss.fund_result = None
        with st.status(f"🏦 {fd_start} 설정 펀드 시뮬레이션...", expanded=True) as status:
            bar = st.progress(0.0, text="유니버스 데이터 로드 준비 중...")
            try:
                result = fund_agent.run(
                    start_date_str=str(fd_start),
                    n_universe=fd_universe,
                    n_top=fd_top,
                    rebalance_days=_rebal_days,
                    weighting=_weight_key,
                    vol_target=0.15 if fd_voltgt else None,
                    stop_atr=2.5 if fd_stop else None,
                    market=ss.market,
                    progress=lambda i, t, name: bar.progress(
                        i / t, text=f"데이터 로드 {i}/{t} — {name}"
                    ),
                )
                try:
                    fund_agent.save_log(result)
                    result["logged"] = True
                    ss.fund_logs = None   # 다음 조회 시 새로 로드
                except Exception:
                    result["logged"] = False
                ss.fund_result = result
                _global_store()["fund_result"] = result
                status.update(
                    label=f"✅ 완료 — {result['metrics']['n_days']}거래일 운용, "
                          f"리밸런싱 {result['metrics']['n_rebalances']}회",
                    state="complete", expanded=False,
                )
            except Exception as e:
                st.error(f"시뮬레이션 오류: {e}")
                ss.fund_result = None

    fres = ss.fund_result
    if fres:
        m = fres["metrics"]
        daily = fres["daily"]
        _p = fres["params"]
        _is_us = _p.get("market") == "US"
        _bm_name = "S&P500" if _is_us else "KOSPI"
        _pxfmt = (lambda v: f"${v:,.2f}") if _is_us else (lambda v: f"{v:,.0f}")

        # ── 성과 카드 ─────────────────────────────────────────────────
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("누적수익률", f"{m['cum_return']:+.1%}")
        c2.metric(f"{_bm_name} 대비 초과", f"{m['excess']:+.1%}" if m["excess"] is not None else "N/A")
        c3.metric("최대낙폭 (MDD)", f"{m['mdd']:.1%}")
        c4.metric("샤프", f"{m['sharpe']:.2f}" if m["sharpe"] is not None else "N/A")
        _cagr = f"연환산 {m['cagr']:+.1%}" if m["cagr"] is not None else ""
        st.caption(
            f"{fres['start_date']} ~ {fres['end_date']} · {m['n_days']}거래일 · "
            f"{_cagr} · 리밸런싱 {m['n_rebalances']}회 · 평균 회전율 {m['avg_turnover']:.0%}"
            + (f" · 손절 매도 누적 {m['stop_traded']:.0%}" if m.get("n_stops") else "")
            + f" · 후보 풀 {fres['n_scanned']}종목 (리밸런싱마다 유니버스 재탐색)"
        )
        st.caption(
            f"실행 조건: {'🇺🇸 S&P500' if _is_us else '🇰🇷 국장'} · "
            f"{_p['n_top']}종목 · {_p['rebalance_days']}거래일 주기 · "
            f"{fund_agent.WEIGHT_LABELS.get(_p['weighting'], _p['weighting'])} · "
            f"변동성타겟 {'ON' if _p.get('vol_target') else 'OFF'} · "
            f"손절 {'ON' if _p.get('stop_atr') else 'OFF'}"
        )
        if fres.get("logged") is True:
            st.caption("📝 이 실행 결과가 Google Sheets `_펀드시뮬로그` 탭에 저장되었습니다.")
        elif fres.get("logged") is False:
            st.caption("⚠️ 로그 저장 실패 — Google Sheets 연결(secrets)을 확인하세요.")

        # ── 기준가 차트 (vs KOSPI) ────────────────────────────────────
        dates = [d["date"] for d in daily]
        navs  = [d["nav"] * 1000 for d in daily]
        fig_f = go.Figure()
        fig_f.add_scatter(x=dates, y=navs, name="펀드 기준가", mode="lines",
                          line=dict(color="#d32f2f", width=2))
        kospi0 = next((d["kospi"] for d in daily if d["kospi"] is not None), None)
        if kospi0:
            fig_f.add_scatter(
                x=dates,
                y=[d["kospi"] / kospi0 * 1000 if d["kospi"] is not None else None for d in daily],
                name=f"{_bm_name} (1,000 환산)", mode="lines",
                line=dict(color="rgba(128,128,128,0.7)", width=1.5, dash="dot"),
            )
        fig_f.add_hline(y=1000, line_dash="dash", line_color="gray", line_width=1)
        fig_f.update_layout(
            height=340, margin=dict(l=0, r=0, t=20, b=0),
            yaxis_title="기준가 (원)",
            legend=dict(orientation="h", y=1.1),
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        )
        fig_f.update_yaxes(gridcolor="rgba(128,128,128,0.15)")
        st.plotly_chart(fig_f, use_container_width=True)

        # ── 날짜별 운용 현황 ──────────────────────────────────────────
        st.markdown("#### 날짜별 운용 현황")
        sel_date = st.select_slider("조회 날짜", options=dates, value=dates[-1], key="fd_sel_date")
        sel_day = next(d for d in daily if d["date"] == sel_date)
        ev = None
        for r in fres["rebalances"]:
            if r["date"] <= sel_date:
                ev = r
            else:
                break

        st.markdown(
            f"**기준가 {sel_day['nav'] * 1000:,.0f}원** "
            f"({(sel_day['nav'] - 1) * 100:+.1f}%)"
            + (f" · 주식비중 {ev['equity_frac']:.0%}" if ev else "")
        )
        if ev:
            pos_px = fres["positions_daily"].get(sel_date, {})
            h_rows = []
            for h in ev["holdings"]:
                cur = pos_px.get(h["code"])
                h_rows.append({
                    "종목":       f"{h['name']}({h['code']})",
                    "비중(설정)": f"{h['weight']:.0%}",
                    "편입일":     ev["date"],
                    "편입가":     _pxfmt(h["price"]),
                    "조회일 종가": _pxfmt(cur) if cur else "—",
                    "수익률":     f"{(cur / h['price'] - 1):+.1%}" if cur else "—",
                    "편입시 점수": f"{h['score']:+.2f}",
                })
            st.dataframe(pd.DataFrame(h_rows), hide_index=True, use_container_width=True)
            if ev["date"] == sel_date and (ev["entries"] or ev["exits"]):
                _in  = ", ".join(ev["entries"]) or "없음"
                _out = ", ".join(ev["exits"]) or "없음"
                st.caption(f"🔄 이날 리밸런싱 — 편입: {_in} · 편출: {_out}")

        # ── 손절 발동 내역 ────────────────────────────────────────────
        if fres.get("stops"):
            with st.expander(f"⛔ 손절 발동 내역 ({len(fres['stops'])}회)"):
                stop_rows = [{
                    "날짜":   s["date"],
                    "종목":   f"{s['name']}({s['code']})",
                    "편입가": _pxfmt(s["entry"]),
                    "손절가": _pxfmt(s["exit"]),
                    "손실률": f"{s['loss_pct']:+.1%}",
                } for s in fres["stops"]]
                st.dataframe(pd.DataFrame(stop_rows), hide_index=True, use_container_width=True)

        # ── 리밸런싱 히스토리 ─────────────────────────────────────────
        with st.expander(f"리밸런싱 히스토리 ({m['n_rebalances']}회)"):
            r_rows = []
            for i, r in enumerate(fres["rebalances"]):
                r_rows.append({
                    "날짜":     r["date"],
                    "보유":     ", ".join(h["name"] for h in r["holdings"]),
                    "편입":     ", ".join(r["entries"]) if i > 0 else "(최초 설정)",
                    "편출":     ", ".join(r["exits"]) or "—",
                    "회전율":   f"{r['turnover']:.0%}",
                    "주식비중": f"{r['equity_frac']:.0%}",
                })
            st.dataframe(pd.DataFrame(r_rows), hide_index=True, use_container_width=True)

        st.caption(
            "⚠️ 생존 편향(현재 상장 종목 기준) · 리밸런싱은 기술점수만 사용 (뉴스·펀더멘탈은 과거 재현 불가) · "
            "종가 체결 가정, 거래비용 편도 0.3% 반영"
        )
    else:
        st.info("왼쪽 사이드바에서 설정일과 운용 방식을 선택하고 **펀드 시뮬레이션 실행** 버튼을 눌러주세요.")

    # ── 시뮬레이션 로그 조회 ──────────────────────────────────────────────
    with st.expander("📝 시뮬레이션 로그 (지난 실행 기록 비교)"):
        if st.button("로그 불러오기", key="fd_load_logs"):
            try:
                ss.fund_logs = fund_agent.load_logs()
            except Exception as e:
                ss.fund_logs = None
                st.error(f"로그 로드 실패: {e}")
        logs = ss.get("fund_logs")
        if logs:
            df_logs = pd.DataFrame(logs).iloc[::-1]  # 최신이 위로
            st.dataframe(df_logs, hide_index=True, use_container_width=True)
            st.caption(
                f"총 {len(logs)}건 · 매 실행마다 자동 저장 · "
                "같은 기간을 비중 방식/주기만 바꿔 여러 번 돌리면 이 표에서 바로 비교할 수 있습니다."
            )
        elif logs is not None:
            st.caption("저장된 로그가 없습니다. 시뮬레이션을 실행하면 자동으로 기록됩니다.")

elif mode == MODE_DISCOUNT:
    st.title("💎 할인찬스 파인더")
    st.caption(
        "가치 대비 싸게 거래되는 종목을 찾습니다 — 저PER·저PBR(고든모형 적정가 대비) 중 "
        "재무가 건강하고(F-Score) 하락이 멈춘 종목만. 근거: Piotroski(2000) 저평가×고품질 조합."
    )
    with st.expander("📖 할인점수는 뭘 참고해서 계산하나요?"):
        st.markdown(
            "**데이터 출처** — 네이버 시총 상위 목록(PER 1차 컷), 네이버 종목 페이지"
            "(PER·업종PER·PBR·배당·분기ROE), DART 전자공시 사업보고서(F-Score·부채비율·성장률), "
            "네이버 차트 API(52주 고점·기술점수). LLM 미사용, 전부 규칙 기반.\n\n"
            "**할인점수 100점의 구성**\n"
            "- **밸류에이션 50점**: PER 절대 수준 + 업종 PER 대비 할인율(Damodaran 2012) + "
            "PBR을 고든 성장모형 적정가(ROE÷요구수익률 8%)와 비교 — 'ROE가 높은 회사는 PBR이 "
            "높아도 싼 것'을 반영\n"
            "- **품질 30점**: ROE 수준·추이 + Piotroski F-Score(수익성 4·건전성 3·효율 2, 총 9개 "
            "이진 판정) — **가치함정 방지**: 싸면서 재무가 나빠지는 회사 제거 (Piotroski 2000)\n"
            "- **안전마진 20점**: 52주 고점 대비 할인폭(10) + 바닥 안정화(10: 기술점수·20일선) — "
            "**낙하는 칼 방지**: 아직 추락 중인 종목 제외\n\n"
            "**자동 제외**: 적자(PER≤0) · F-Score ≤3(재무 악화) · 기술점수 <−30(하락 지속). "
            "제외 내역과 사유는 결과 하단에 표시됩니다."
        )

    if dc_btn:
        ss.dc_result = None
        with st.status("💎 할인찬스 탐색 중...", expanded=True) as status:
            bar = st.progress(0.0, text="시총 상위 목록 수집 중...")
            try:
                result = discount_agent.run(
                    per_max=float(dc_per_max),
                    n_scan=int(dc_scan),
                    n_top=int(dc_top),
                    progress=lambda i, t, name: bar.progress(
                        i / t, text=f"정밀 분석 {i}/{t} — {name}"
                    ),
                )
                ss.dc_result = result
                _global_store()["dc_result"] = result
                status.update(
                    label=f"✅ 완료 — 저PER 후보 {result['n_cheap']}종목 중 "
                          f"{result['n_scanned']}종목 정밀 분석",
                    state="complete", expanded=False,
                )
            except Exception as e:
                st.error(f"탐색 오류: {e}")
                ss.dc_result = None

    dres = ss.dc_result
    if dres:
        st.markdown(
            f"**{dres['run_date']} 기준** · 풀 {dres['n_pool']}종목 → "
            f"PER ≤ {dres['per_max']:.0f} 흑자 {dres['n_cheap']}종목 → "
            f"정밀 분석 {dres['n_scanned']}종목 → 통과 {len(dres['rows'])}종목 "
            f"(제외 {len(dres['excluded'])}종목)"
        )

        if not dres["rows"]:
            st.warning("조건을 통과한 종목이 없습니다. 1차 PER 컷을 높여보세요.")
        else:
            tbl = []
            for r in dres["rows"]:
                per_txt = f"{r['per']:.1f}" if r["per"] else "—"
                if r["per"] and r["sector_per"]:
                    rel = (r["sector_per"] - r["per"]) / r["sector_per"] * 100
                    per_txt += f" ({rel:+.0f}%)"
                tbl.append({
                    "종목":      f"{r['name']}({r['code']})",
                    "할인점수":   r["score"],
                    "밸류":      f"{r['val_score']}/50",
                    "품질":      f"{r['qual_score']}/30",
                    "안전마진":   f"{r['margin_score']}/20",
                    "PER(업종比)": per_txt,
                    "PBR":       f"{r['pbr']:.2f}" if r["pbr"] else "—",
                    "ROE":       f"{r['roe']:.1f}%" if r["roe"] else "—",
                    "배당":      f"{r['div_yield']:.1f}%" if r["div_yield"] else "—",
                    "52주고점比": f"{r['discount52']:+.0%}",
                    "F-Score":   f"{r['f_score']}/9" if r["f_score"] is not None else "—",
                })
            st.dataframe(pd.DataFrame(tbl), hide_index=True, use_container_width=True)
            st.caption(
                "할인점수 = 밸류에이션(50) + 품질(30) + 안전마진(20) · "
                "PER 옆 괄호 = 업종 대비 할인율 · F-Score — 는 DART 미확보"
            )

            # ── 종목별 상세 ───────────────────────────────────────────
            for r in dres["rows"]:
                with st.expander(f"💎 {r['name']} — 할인점수 {r['score']} · {r['close']:,.0f}원"):
                    if r.get("narrative"):
                        st.info(f"🧾 {r['narrative']}")
                    st.write(r["summary"])
                    st.caption(f"안전마진: {r['margin_reason']} · 데이터: {r['data_quality']}")
                    if r["f_details"]:
                        for line in r["f_details"]:
                            st.caption(line)
                    st.button("🔍 종목 분석으로 이동", key=f"dc_go_{r['code']}",
                              on_click=goto_detail, args=(r["code"],))

        if dres["excluded"]:
            with st.expander(f"🚫 제외된 종목 ({len(dres['excluded'])}건) — 싸 보여도 거른 이유"):
                for e in dres["excluded"]:
                    st.caption(f"- **{e['name']}**({e['code']}): {e['reason']}")
    else:
        st.info("왼쪽 사이드바에서 조건을 정하고 **할인찬스 탐색** 버튼을 눌러주세요.")

elif mode == MODE_TRACK:
    _tmkt = ss.market
    st.title(f"📜 트랙레코드 — {'🇺🇸 S&P500' if _tmkt == 'US' else '🇰🇷 국장'}")
    st.caption(
        "매일 아침 7시 브리핑이 `_트랙레코드` 시트에 기록한 기술점수 top5의 실제 성과입니다. "
        "수익률 = 추천일 종가 → 현재가 · 초과수익 = 같은 기간 벤치마크 대비."
    )
    ss.setdefault("track_result", None)

    if tr_btn:
        with st.status("📜 트랙레코드 채점 중... (기록 종목 시세 조회)", expanded=True) as _ts:
            _tbar = st.progress(0.0, text="기록 로드 중...")
            try:
                ss.track_result = track_record.evaluate(
                    market=_tmkt,
                    progress=lambda i, t, n: _tbar.progress(i / t, text=f"{i}/{t} — {n}"),
                )
                ss.track_result["market"] = _tmkt
                _ts.update(label="✅ 채점 완료", state="complete", expanded=False)
            except Exception as e:
                st.error(f"채점 실패: {e}")
                ss.track_result = None

    _tres = ss.get("track_result")
    if _tres and _tres.get("market") == _tmkt:
        _tsum = _tres["summary"]
        if not _tsum:
            st.info(
                "아직 이 시장의 기록이 없습니다. 매일 아침 브리핑이 실행되면 자동으로 쌓입니다 "
                "(GitHub Actions → daily-alert)."
            )
        else:
            _tfmt = (lambda v: f"${v:,.2f}") if _tmkt == "US" else (lambda v: f"{v:,.0f}원")
            t1, t2, t3, t4 = st.columns(4)
            t1.metric("표본", f"{_tsum['n']}픽 · {_tsum['n_days']}일")
            t2.metric("평균 수익률", f"{_tsum['avg_ret']:+.1%}")
            t3.metric("승률", f"{_tsum['win_rate']:.0%}")
            t4.metric("벤치 대비 평균 초과",
                      f"{_tsum['avg_excess']:+.1%}" if _tsum["avg_excess"] is not None else "N/A",
                      f"초과 승률 {_tsum['excess_win']:.0%}" if _tsum["excess_win"] is not None else None,
                      delta_color="off")
            st.caption(
                "🎯 핵심 지표는 **벤치 대비 초과**입니다 — 이게 지속적으로 +면 스크리너에 엣지가 있다는 뜻, "
                "−면 지수를 사는 게 낫다는 뜻. 표본 30픽 미만에선 판단 보류."
            )

            _trows = [{
                "추천일":  x["date"],
                "순위":    x["rank"],
                "종목":    f"{x['name']}({x['code']})",
                "점수":    f"{x['score']:+.2f}",
                "추천가":  _tfmt(x["entry"]),
                "현재가":  _tfmt(x["current"]),
                "수익률":  f"{x['ret']:+.1%}",
                "벤치比":  f"{x['excess']:+.1%}" if x["excess"] is not None else "—",
                "경과일":  x["days"],
            } for x in _tres["rows"]]
            st.dataframe(pd.DataFrame(_trows), hide_index=True, use_container_width=True)
    else:
        st.info("왼쪽 사이드바에서 **성과 채점 실행**을 눌러주세요. (기록은 매일 아침 자동 축적)")

render_disclaimer()

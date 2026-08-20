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

from agents import (community, fundamental_agent, news_agent, portfolio_agent,
                    scoring, screener, strategy_agent, technical_agent)

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

MODE_DETAIL    = "🔍 종목 분석"
MODE_SCREEN    = "🏆 오늘의 후보 종목"
MODE_PORTFOLIO = "💼 포트폴리오"

ss = st.session_state
ss.setdefault("results", {})
ss.setdefault("last_analyzed", None)
ss.setdefault("screener", None)
ss.setdefault("bt_open", None)      # 현재 열린 백테스트 종목코드
ss.setdefault("bt_cache", {})       # {code: run_multiperiod_for_code 결과}
ss.setdefault("pf_quick_add", None) # 포트폴리오 빠른 추가 대상 코드
if "code_input" not in ss:
    ss.code_input = "005930"
if "mode" not in ss:
    ss.mode = MODE_DETAIL


def goto_detail(code: str) -> None:
    ss["mode"] = MODE_DETAIL
    ss["code_input"] = code
    ss["pending"] = code


# ── 사이드바 ────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("📈 K-TradingAgents")
    st.caption("멀티 에이전트 한국 주식 분석 v4")

    mode = st.radio("모드", [MODE_DETAIL, MODE_SCREEN, MODE_PORTFOLIO], key="mode")
    st.divider()

    if mode == MODE_DETAIL:
        code_in = st.text_input("종목코드 (6자리)", key="code_input",
                                help="예: 삼성전자 005930, SK하이닉스 000660")
        run_btn = st.button("🚀 에이전트 분석 실행", type="primary", use_container_width=True)
        st.markdown(
            "**자주 찾는 종목**\n\n"
            "- 삼성전자 `005930`\n"
            "- SK하이닉스 `000660`\n"
            "- 현대차 `005380`\n"
            "- NAVER `035420`\n"
            "- 카카오 `035720`"
        )
        scan_btn = False
    elif mode == MODE_SCREEN:
        n_liq = st.slider("1단계: 거래대금 상위 종목 수", 50, 300, 200, 10,
                          help="네이버 시총 상위 목록에서 거래대금 순으로 자르는 1차 유동성 필터")
        n_full = st.slider("2단계: 풀 분석 종목 수", 3, 30, 10,
                           help="1단계 기술 점수 상위 종목만 뉴스+펀더멘탈 포함 풀 분석")
        scan_btn = st.button("📡 오늘의 후보 스캔", type="primary", use_container_width=True)
        st.caption(
            "1단계는 종목당 요청 1번(차트 API)이라 가볍고, "
            "뉴스·펀더멘탈 크롤링이 필요한 풀 분석은 상위 종목에만 실행됩니다. LLM 토큰은 쓰지 않습니다."
        )
        run_btn = False
    else:  # MODE_PORTFOLIO
        scan_btn = False
        run_btn   = False
        st.caption("Google Sheets에 저장된 매수 포지션을 관리합니다.")
        st.caption("손절선 = 매수가 − 1.5×ATR  |  목표가 = 매수가 + 2.0×ATR")


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


# ── 모드: 종목 분석 ─────────────────────────────────────────────────────
if mode == MODE_DETAIL:
    st.title("한국 주식 멀티 에이전트 분석")
    st.caption("Agent 1 (뉴스/공시) → Agent 2 (기술 4축) → Agent 3 (펀더멘탈) → Agent 4 (전략 종합)")

    target, force = None, False
    if run_btn:
        target, force = code_in, True
    pending = ss.pop("pending", None)
    if pending:
        target, force = pending, False

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
                                     "승률": "—", "평균수익률": "—", "평균상승": "—",
                                     "평균하락": "—", "기대값": "—", "무신호 평균": "—"})
                    else:
                        rows.append({
                            "기간": p["label"],
                            "신호수": p["n_signals"],
                            "승률": f"{p['win_rate']:.0%}",
                            "평균수익률": f"{p['avg_return']:+.1%}",
                            "평균상승": f"{p['avg_win']:+.1%}",
                            "평균하락": f"{p['avg_loss']:+.1%}",
                            "기대값": f"{p['expectancy']:+.1%}",
                            "무신호 평균": f"{p['benchmark']:+.1%}" if p.get("benchmark") is not None else "—",
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
            fc1.text_input("종목코드", placeholder="005930", key="pf_code")
            fc2.text_input("종목명", placeholder="삼성전자", key="pf_name")
            fc3.number_input("매수가 (원)", min_value=1, value=70000, step=100, key="pf_price")
            fc4.number_input("수량 (주)", min_value=1, value=10, step=1, key="pf_qty")
            fc5.text_input("매수일", placeholder="2025-01-15", key="pf_date")
            submitted = st.form_submit_button("추가", type="primary", use_container_width=True)
            if submitted:
                code6 = re.sub(r"\D", "", ss.get("pf_code", "")).zfill(6)
                if len(code6) == 6 and code6.isdigit():
                    try:
                        portfolio_agent.add_position(
                            portfolio_agent.Position(
                                code=code6,
                                name=ss.get("pf_name", code6),
                                buy_price=float(ss.get("pf_price", 0)),
                                quantity=int(ss.get("pf_qty", 0)),
                                buy_date=ss.get("pf_date", ""),
                            ),
                            portfolio=selected_pf,
                        )
                        st.success(f"✅ {ss.get('pf_name', code6)} 추가됐습니다.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"추가 실패: {e}")
                else:
                    st.warning("종목코드 6자리를 정확히 입력해주세요.")

    if not positions:
        st.info(f"**{selected_pf}**에 포지션이 없습니다. 위 폼에서 추가해주세요.")
    else:
        # ── 신호 계산 ──────────────────────────────────────────────────
        with st.spinner("시세·ATR·전략점수 계산 중..."):
            signals = portfolio_agent.calc_all_signals(positions)

        # ── 요약 메트릭 ────────────────────────────────────────────────
        total_invest = sum(s.buy_price * s.quantity for s in signals)
        total_eval   = sum(s.eval_amount for s in signals)
        total_pl     = total_eval - total_invest
        total_pl_pct = total_pl / total_invest * 100 if total_invest else 0

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("포지션 수", f"{len(signals)}개")
        m2.metric("총 매수금액", f"{total_invest:,.0f}원")
        m3.metric("총 평가금액", f"{total_eval:,.0f}원")
        m4.metric("총 손익", f"{total_pl:+,.0f}원", f"{total_pl_pct:+.1f}%",
                  delta_color="normal" if total_pl >= 0 else "inverse")

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
            row[2].markdown(f"{sig.buy_price:,.0f}")
            row[3].markdown(f"{sig.current_price:,.0f}" if sig.current_price else "—")
            row[4].markdown(
                f"<span style='color:{rp_color}; font-weight:700'>{sig.return_pct:+.1f}%</span>",
                unsafe_allow_html=True,
            )
            row[5].markdown(f"{sig.stop_loss:,.0f}" if sig.stop_loss else "—")
            row[6].markdown(f"{sig.target1:,.0f}" if sig.target1 else "—")
            row[7].markdown(f"{sig.target2:,.0f}" if sig.target2 else "—")
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
                    d1.metric("평가금액", f"{sig.eval_amount:,.0f}원")
                    d2.metric("손익", f"{sig.profit_loss:+,.0f}원", f"{sig.return_pct:+.1f}%",
                              delta_color="normal" if sig.profit_loss >= 0 else "inverse")
                    d3.metric("ATR(14)", f"{sig.atr:,.0f}원")
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

render_disclaimer()

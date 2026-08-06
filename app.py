# -*- coding: utf-8 -*-
"""
한국 주식 멀티 에이전트 분석 (TradingAgents 스타일) v3
- 🔍 종목 분석: 종목코드 입력 → 3-에이전트 파이프라인 상세 분석
- 🏆 오늘의 후보 종목: 전 시장 2단계 스크리닝 → 종합 점수 랭킹 → 클릭 시 상세로 이동

실행:  streamlit run app.py
"""
import re

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from agents import community, news_agent, scoring, screener, strategy_agent, technical_agent

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

MODE_DETAIL = "🔍 종목 분석"
MODE_SCREEN = "🏆 오늘의 후보 종목"

ss = st.session_state
ss.setdefault("results", {})        # code -> (news, tech, strat)
ss.setdefault("last_analyzed", None)
ss.setdefault("screener", None)
if "code_input" not in ss:
    ss.code_input = "005930"
if "mode" not in ss:
    ss.mode = MODE_DETAIL


def goto_detail(code: str) -> None:
    """후보 종목 행에서 '상세 →' 클릭 시: 모드 전환 + 해당 종목 표시."""
    ss["mode"] = MODE_DETAIL
    ss["code_input"] = code
    ss["pending"] = code


# ── 사이드바 ────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("📈 K-TradingAgents")
    st.caption("멀티 에이전트 한국 주식 분석 v3")

    mode = st.radio("모드", [MODE_DETAIL, MODE_SCREEN], key="mode")
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
    else:
        n_liq = st.slider("1단계: 거래대금 상위 종목 수", 50, 300, 120, 10,
                          help="네이버 시총 상위 목록에서 거래대금 순으로 자르는 1차 유동성 필터")
        n_full = st.slider("2단계: 풀 분석 종목 수", 3, 30, 10,
                           help="1단계 기술 점수 상위 종목만 뉴스 수집 포함 풀 분석")
        scan_btn = st.button("📡 오늘의 후보 스캔", type="primary", use_container_width=True)
        st.caption(
            "1단계는 종목당 요청 1번(차트 API)이라 가볍고, "
            "뉴스 크롤링이 필요한 풀 분석은 상위 종목에만 실행됩니다. LLM 토큰은 쓰지 않습니다."
        )
        run_btn = False


# ── 파이프라인 실행 ─────────────────────────────────────────────────────
def analyze(code: str):
    with st.status("에이전트 파이프라인 실행 중...", expanded=True) as status:
        st.write("🗞️ **Agent 1** — 네이버 증권 뉴스/공시 수집 중...")
        news = news_agent.run(code)
        st.write(f"→ {news.stock_name}({news.code}) 기사/공시 {news.raw_count}건 → {len(news.items)}개 사건")

        st.write("📊 **Agent 2** — 시세 수집, 4축(추세/모멘텀/거래량/위치) 분석 중...")
        tech = technical_agent.run(code)
        st.write("→ " + (tech.error or tech.summary))

        st.write("🧠 **Agent 3** — 종합 판단 및 매매 가격 산출 중...")
        strat = strategy_agent.run(news, tech)

        st.write("💬 커뮤니티 관심 지표 수집 중... (참고용)")
        try:
            comm = community.get(code)
        except Exception:
            comm = None
        status.update(label="분석 완료 ✅", state="complete", expanded=False)
    return news, tech, strat, comm


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


def render_analysis(news, tech, strat, comm=None):
    st.subheader(f"{news.stock_name} ({news.code})")

    df = tech.df
    prev_close = float(df["close"].iloc[-2]) if len(df) >= 2 else tech.price
    change = tech.price - prev_close
    change_pct = change / prev_close * 100 if prev_close else 0

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("현재가", f"{tech.price:,.0f}원", f"{change:+,.0f}원 ({change_pct:+.2f}%)")
    c2.metric("RSI (14)", f"{tech.rsi:.1f}")
    c3.metric("ATR", f"{tech.atr:,.0f}원", f"{tech.atr_pct:.1%} 진폭", delta_color="off")
    c4.metric("뉴스 감성 (참고)", f"{news.score:+.2f}", help="뉴스는 점수에 반영되지 않는 확인용 지표입니다. 강한 악재 공시의 매수 베토만 유지됩니다.")
    c5.metric("기술 원점수", f"{tech.score:+.2f}")
    c6.metric("투자 매력 점수", f"{strat.score100}점", strat.grade, delta_color="off",
              help="원점수를 과거 4만 종목-일 분포의 백분위로 변환한 값 — 아래 '점수 산출 정보' 참고")

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

    st.markdown("### 🧠 Agent 3 — 최종 전략")
    left, right = st.columns([1, 2])
    with left:
        sub = f'<div style="color:gray; font-size:0.85rem;">{strat.reason_code}</div>' if strat.reason_code else ""
        color = score_color(strat.score100)
        st.markdown(
            f'<div class="agent-card"><div class="agent-title">투자 매력 점수</div>'
            f'<div style="font-size:2.6rem; font-weight:800; color:{color}; line-height:1.1;">'
            f'{strat.score100}<span style="font-size:1.1rem; color:gray;"> / 100 · {strat.grade}</span></div>'
            f'<div style="margin-top:0.3rem;">참고 의견: <b style="color:{color}">{strat.opinion}</b></div>{sub}'
            f'<div style="margin-top:0.3rem; font-size:0.85rem;">{strat.confidence}</div></div>',
            unsafe_allow_html=True,
        )

        # 점수 산출 정보 — 점수가 어디서 왔는지 검증 가능한 근거
        m = scoring.meta()
        band = strat.band or {}
        st.markdown(
            f'<div class="agent-card" style="font-size:0.85rem;">'
            f'<div class="agent-title" style="font-size:0.95rem;">📖 점수 산출 정보</div>'
            f'<div>① 원점수 <b>{strat.total_score:+.2f}</b> = 기술 4축(추세 35/모멘텀 25/거래량 25/위치 15%) '
            f'가중 합 × 합의 배수 {tech.confluence_mult:.2f} — LLM 없이 수식으로만 계산 (환각 없음)</div>'
            f'<div>② 점수 <b>{strat.score100}점</b> = 과거 {m["n_samples"]:,} 종목-일'
            f'({m["n_stocks"]}종목, ~{m["generated"]}) 분포에서 <b>상위 {100 - strat.score100}%</b></div>'
            f'<div>③ 이 점수 구간({band.get("lo", "?")}~{band.get("hi", "?")}점)의 과거 {m["horizon"]}일 후: '
            f'승률 <b>{band.get("win", 0):.0%}</b>, 평균 <b>{band.get("avg", 0):+.1%}</b> '
            f'(표본 {band.get("n", 0):,}건 · 전체 기준선 {m["baseline_win"]:.0%}/{m["baseline_avg"]:+.1%})</div>'
            f'<div style="color:gray;">④ 뉴스·커뮤니티는 점수 미반영(참고용). 과거 통계는 성과를 보장하지 않습니다.</div>'
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

        st.markdown("**판단 기여도**")
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
            st.caption(
                "동일 종목 과거 구간에 근사 점수 체계를 적용한 참고 통계입니다. "
                "표본이 적고 문맥형 RSI·합의 배수는 단순화되어 있어 성과를 보장하지 않습니다."
            )

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
    st.caption("Agent 1 (뉴스/공시) → Agent 2 (기술 4축 분석) → Agent 3 (전략 종합) 파이프라인")

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
        news, tech, strat = cached[:3]
        comm = cached[3] if len(cached) > 3 else None
        if strat.error:
            st.error(strat.error)
        else:
            render_analysis(news, tech, strat, comm)
    else:
        st.info("왼쪽 사이드바에서 종목코드를 입력하고 **에이전트 분석 실행** 버튼을 눌러주세요.")

# ── 모드: 오늘의 후보 종목 ──────────────────────────────────────────────
else:
    st.title("🏆 오늘의 후보 종목")
    st.caption("1단계: 거래대금 상위 종목 근사 기술 점수 → 2단계: 상위 종목만 뉴스 포함 풀 분석 → 종합 점수 랭킹")

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
            ss.results[c.code] = (c.news, c.tech, c.strat, c.comm)

    sc = ss.screener
    if not sc:
        st.info("왼쪽 사이드바에서 **오늘의 후보 스캔** 버튼을 눌러주세요. "
                "1단계는 종목당 요청 1번이라 1~2분 내에 끝납니다.")
    else:
        final = sc["final"]
        st.markdown(f"**풀 분석 {len(final)}종목 — 투자 매력 점수 순** "
                    f"(1단계 {sc['params'][0]}종목 → 2단계 {sc['params'][1]}종목)")

        widths = [0.5, 2.0, 1.1, 1.0, 1.0, 1.7, 1.6, 0.9]
        hdr = st.columns(widths)
        for col, label in zip(hdr, ["#", "종목", "현재가", "점수", "등급/의견", "커뮤니티(참고)", "매수가 / 손절가", ""]):
            col.markdown(f"**{label}**")

        for i, c in enumerate(final, 1):
            s = c.strat
            color = score_color(s.score100)
            buzz = ""
            if c.comm is not None:
                parts = []
                if c.comm.search_rank:
                    parts.append(f"🔥 검색 {c.comm.search_rank}위")
                if c.comm.board_ratio is None and c.comm.board_today:
                    parts.append("토론실 급증")
                elif c.comm.board_ratio and c.comm.board_ratio >= 2:
                    parts.append(f"토론실 ×{c.comm.board_ratio}")
                buzz = " · ".join(parts) or "-"
            cols = st.columns(widths)
            cols[0].markdown(f"<div class='rank-row'>{i}</div>", unsafe_allow_html=True)
            cols[1].markdown(f"<div class='rank-row'><b>{c.name}</b> <span style='color:gray'>{c.code}</span></div>",
                             unsafe_allow_html=True)
            cols[2].markdown(f"<div class='rank-row'>{c.tech.price:,.0f}원</div>", unsafe_allow_html=True)
            cols[3].markdown(f"<div class='rank-row' style='color:{color}; font-weight:800; font-size:1.1rem'>{s.score100}</div>",
                             unsafe_allow_html=True)
            cols[4].markdown(f"<div class='rank-row'>{s.grade} <span style='color:gray; font-size:0.85rem'>({s.opinion})</span></div>",
                             unsafe_allow_html=True)
            cols[5].markdown(f"<div class='rank-row'>{buzz}</div>", unsafe_allow_html=True)
            cols[6].markdown(f"<div class='rank-row'>{s.buy_price:,.0f} / {s.stop_loss:,.0f}</div>",
                             unsafe_allow_html=True)
            cols[7].button("상세 →", key=f"detail_{c.code}", on_click=goto_detail, args=(c.code,))

        st.caption("점수 = 과거 4만 종목-일 분포 백분위 (상세 화면의 '점수 산출 정보' 참고). "
                   "커뮤니티·뉴스는 점수에 반영되지 않는 참고 지표입니다. '상세 →'를 누르면 캐시된 풀 분석으로 이동합니다.")

        if sc.get("top_search"):
            with st.expander("🔥 네이버 검색상위 30 — 지금 커뮤니티가 보고 있는 종목 (참고)"):
                ts_df = pd.DataFrame(
                    [{"순위": t.rank, "종목명": t.name, "코드": t.code, "검색비율": t.ratio}
                     for t in sc["top_search"]]
                )
                st.dataframe(ts_df, hide_index=True, use_container_width=True)
                st.caption("검색 관심은 급등주와 급락주 모두에서 치솟습니다 — 방향이 아니라 '주목도'의 지표입니다.")

render_disclaimer()

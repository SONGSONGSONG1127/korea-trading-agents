# -*- coding: utf-8 -*-
"""
미장 전용 펀더멘탈 분석 (USA-TradingAgents)

국장(fundamental_agent)과 다른 미장 표준 접근:
  - 절대 PER 임계값 대신 **섹터 forward PER 기준 대비 할인/프리미엄** (Yardeni/FactSet)
  - 회계이익보다 **FCF yield** (미국 기관 표준), EV/EBITDA
  - 주주환원 = 배당 + **자사주 매입** (Shareholder Yield — 미국은 자사주가 배당보다 큼)
  - 품질: Gross Margin (Novy-Marx 2013 gross profitability) + ROE + 영업마진
  - 성장 조정: PEG (Lynch)

할인점수 구성 (0~100):
  밸류 35   : 섹터 대비 fwd PER(14) + EV/EBITDA(7) + FCF yield(8) + PEG(6)
  품질 35   : ROE(10) + 매출총이익률(9) + 영업마진(8) + 부채 D/E(8)
  성장·환원 30: 매출성장(8) + EPS성장(8) + Shareholder Yield(14)

섹터 forward PER 기준표: 2026-08 실측 (FactSet Earnings Insight, Yardeni, Siblis).
시장 환경 변화 시 이 표만 갱신하면 된다.
"""
from __future__ import annotations

# 2026-08 기준 섹터 forward P/E (출처: FactSet/Yardeni/Siblis)
SECTOR_FWD_PE = {
    "Information Technology": 29.0, "Technology": 29.0,
    "Industrials":            25.2,
    "Consumer Discretionary": 24.3, "Consumer Cyclical": 24.3,
    "Consumer Staples":       22.2, "Consumer Defensive": 22.2,
    "Communication Services": 21.0,
    "Health Care":            18.7, "Healthcare": 18.7,
    "Materials":              18.5, "Basic Materials": 18.5,
    "Utilities":              17.6,
    "Real Estate":            17.5,
    "Financials":             15.5, "Financial Services": 15.5,
    "Energy":                 13.1,
}
MARKET_FWD_PE = 20.0  # S&P500 전체 (FactSet, 2026-08)


def _cashflow_returns(tk) -> tuple[float | None, float | None]:
    """연간 현금흐름표에서 (배당 지급액, 자사주 매입액) — 절대값, USD."""
    try:
        cf = tk.cashflow
        div = buy = None
        for label in cf.index:
            l = str(label).lower()
            if "dividends paid" in l or l == "cash dividends paid":
                div = abs(float(cf.loc[label].iloc[0]))
            if "repurchase of capital stock" in l or "common stock repurchase" in l:
                buy = abs(float(cf.loc[label].iloc[0]))
        return div, buy
    except Exception:
        return None, None


def run(ticker: str) -> dict:
    """yfinance 재무 데이터 → 미장식 펀더멘탈 점수·지표·해석."""
    import yfinance as yf
    tk = yf.Ticker(ticker)
    try:
        info = tk.info or {}
    except Exception:
        info = {}

    sector = info.get("sector") or ""
    mcap = info.get("marketCap")
    fwd_pe = info.get("forwardPE")
    trl_pe = info.get("trailingPE")
    ev_ebitda = info.get("enterpriseToEbitda")
    fcf = info.get("freeCashflow")
    roe = (info.get("returnOnEquity") or 0) * 100 if info.get("returnOnEquity") is not None else None
    gross_m = (info.get("grossMargins") or 0) * 100 if info.get("grossMargins") is not None else None
    op_m = (info.get("operatingMargins") or 0) * 100 if info.get("operatingMargins") is not None else None
    de = info.get("debtToEquity")            # % (예: 45.2)
    rev_g = (info.get("revenueGrowth") or 0) * 100 if info.get("revenueGrowth") is not None else None
    eps_g = (info.get("earningsGrowth") or 0) * 100 if info.get("earningsGrowth") is not None else None
    div_yield = info.get("dividendYield")    # 이미 % (yfinance 0.2.5x)

    fcf_yield = (fcf / mcap * 100) if fcf and mcap else None
    peg = (fwd_pe / eps_g) if fwd_pe and eps_g and eps_g > 0 else None
    sector_pe = SECTOR_FWD_PE.get(sector, MARKET_FWD_PE)
    pe_ratio = (fwd_pe / sector_pe) if fwd_pe and fwd_pe > 0 else None  # <1 = 섹터보다 싸다

    div_paid, buyback = _cashflow_returns(tk)
    sh_yield = None
    if mcap:
        total_ret = (div_paid or 0) + (buyback or 0)
        if total_ret > 0:
            sh_yield = total_ret / mcap * 100

    # ── 밸류 35 ───────────────────────────────────────────────────────────
    v = 0
    if pe_ratio is not None:
        v += 14 if pe_ratio <= 0.70 else 11 if pe_ratio <= 0.85 else 8 if pe_ratio <= 1.0 \
            else 5 if pe_ratio <= 1.15 else 2 if pe_ratio <= 1.35 else 0
    if ev_ebitda is not None and ev_ebitda > 0:
        v += 7 if ev_ebitda < 8 else 5 if ev_ebitda < 11 else 3 if ev_ebitda < 14 else 1 if ev_ebitda < 18 else 0
    if fcf_yield is not None:
        v += 8 if fcf_yield >= 6 else 6 if fcf_yield >= 4 else 4 if fcf_yield >= 2.5 else 2 if fcf_yield >= 1 else 0
    if peg is not None:
        v += 6 if peg < 1.0 else 4 if peg < 1.5 else 2 if peg < 2.0 else 0

    # ── 품질 35 ───────────────────────────────────────────────────────────
    q = 0
    if roe is not None:
        q += 10 if roe >= 25 else 7 if roe >= 15 else 4 if roe >= 8 else 1 if roe >= 0 else 0
    if gross_m is not None:
        q += 9 if gross_m >= 50 else 6 if gross_m >= 35 else 3 if gross_m >= 20 else 0
    if op_m is not None:
        q += 8 if op_m >= 25 else 6 if op_m >= 15 else 3 if op_m >= 8 else 0
    if de is not None:
        q += 8 if de < 50 else 6 if de < 100 else 3 if de < 200 else 0
    else:
        q += 4  # 부채 정보 없음 — 중립 처리

    # ── 성장·환원 30 ──────────────────────────────────────────────────────
    g = 0
    if rev_g is not None:
        g += 8 if rev_g >= 15 else 6 if rev_g >= 8 else 4 if rev_g >= 3 else 2 if rev_g >= 0 else 0
    if eps_g is not None:
        g += 8 if eps_g >= 20 else 6 if eps_g >= 10 else 3 if eps_g >= 0 else 0
    if sh_yield is not None:
        g += 14 if sh_yield >= 5 else 10 if sh_yield >= 3 else 6 if sh_yield >= 1.5 else 3 if sh_yield >= 0.5 else 0

    total = v + q + g
    grade = "우수" if total >= 75 else "양호" if total >= 60 else "보통" if total >= 45 else "주의"

    # ── 미장식 자연어 해석 ────────────────────────────────────────────────
    s: list[str] = []
    if pe_ratio is not None:
        rel = (1 - pe_ratio) * 100
        if rel >= 15:
            s.append(f"섹터 평균(fwd PER {sector_pe:.0f}배) 대비 {rel:.0f}% 할인되어 거래됩니다.")
        elif rel <= -15:
            s.append(f"섹터 평균(fwd PER {sector_pe:.0f}배)보다 {-rel:.0f}% 프리미엄이 붙어 있습니다 — 그만큼의 성장을 증명해야 하는 가격.")
        else:
            s.append(f"섹터 평균(fwd PER {sector_pe:.0f}배) 수준의 무난한 가격입니다.")
    if fcf_yield is not None:
        if fcf_yield >= 4:
            s.append(f"현금 창출력이 좋습니다(FCF 수익률 {fcf_yield:.1f}%) — 미국식 기준으로 실속 있는 장사.")
        elif fcf_yield < 1.5:
            s.append(f"시총 대비 잉여현금이 얇습니다(FCF 수익률 {fcf_yield:.1f}%) — 이익의 질을 확인할 필요.")
    if gross_m is not None and gross_m >= 50:
        s.append(f"매출총이익률 {gross_m:.0f}%의 고마진 구조 — 가격 결정력(해자)의 신호입니다(Novy-Marx).")
    if sh_yield is not None and sh_yield >= 2:
        _bb = f"자사주 {buyback / 1e9:.1f}B$" if buyback else ""
        _dv = f"배당 {div_paid / 1e9:.1f}B$" if div_paid else ""
        s.append(f"연간 {sh_yield:.1f}%를 주주에게 돌려줍니다({' + '.join(x for x in [_bb, _dv] if x)}) — 미장에선 배당보다 이 총환원율이 핵심.")
    if rev_g is not None and eps_g is not None:
        if eps_g > rev_g + 10:
            s.append(f"이익(+{eps_g:.0f}%)이 매출(+{rev_g:.0f}%)보다 빨리 늡니다 — 마진 개선 또는 자사주 효과.")
        elif rev_g < 0:
            s.append(f"매출이 줄고 있습니다({rev_g:.0f}%) — 프리미엄 멀티플과 충돌하는 신호.")
    if de is not None and de > 200:
        s.append(f"부채/자본 {de:.0f}%로 레버리지가 높습니다 — 금리 환경에 민감.")

    return {
        "score": total, "grade": grade,
        "val_score": v, "qual_score": q, "growth_score": g,
        "sector": sector, "sector_pe": sector_pe,
        "fwd_pe": fwd_pe, "trl_pe": trl_pe, "pe_ratio": pe_ratio,
        "ev_ebitda": ev_ebitda, "fcf_yield": fcf_yield, "peg": peg,
        "roe": roe, "gross_m": gross_m, "op_m": op_m, "de": de,
        "rev_g": rev_g, "eps_g": eps_g,
        "div_yield": div_yield, "sh_yield": sh_yield, "buyback": buyback, "div_paid": div_paid,
        "narrative": " ".join(s),
    }

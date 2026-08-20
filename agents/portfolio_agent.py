# -*- coding: utf-8 -*-
"""
포트폴리오 관리 에이전트

Google Sheets 탭(워크시트) 하나 = 계좌(포트폴리오) 하나.
각 탭 컬럼 (1행 = 헤더):
    A:code  B:name  C:buy_price  D:quantity  E:buy_date

신호 판단 기준 (ATR + 기술점수 복합):
  score_series() 반환값 -1~+1 × 100 = -100~+100 스케일
  강한 상승: 40~70, 보통: 10~30, 중립: -10~+10, 약세: -30 이하
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]
HEADER = ["code", "name", "buy_price", "quantity", "buy_date"]
DEFAULT_PORTFOLIO = "기본 계좌"


# ── 데이터 모델 ───────────────────────────────────────────────────────────

@dataclass
class Position:
    code: str
    name: str
    buy_price: float
    quantity: int
    buy_date: str


@dataclass
class PositionSignal:
    code: str
    name: str
    buy_price: float
    quantity: int
    buy_date: str

    current_price: float = 0.0
    atr: float = 0.0
    stop_loss: float = 0.0
    target1: float = 0.0       # 매수가 + 2.0 × ATR
    target2: float = 0.0       # 매수가 + 3.0 × ATR

    return_pct: float = 0.0    # %
    profit_loss: float = 0.0   # 원
    eval_amount: float = 0.0   # 평가금액

    strategy_score: int = 0    # -100 ~ +100 (score_series × 100)
    signal: str = "확인중"     # 손절 / 익절 / 익절 고려 / 보유 / 오류
    signal_reason: str = ""
    error: str = ""


# ── Google Sheets 연결 ────────────────────────────────────────────────────

def _spreadsheet():
    """스프레드시트 객체 반환."""
    import gspread
    from google.oauth2.service_account import Credentials
    import streamlit as st

    creds = Credentials.from_service_account_info(
        dict(st.secrets["gcp_service_account"]),
        scopes=SCOPES,
    )
    gc = gspread.authorize(creds)
    return gc.open_by_key(st.secrets["GSHEET_ID"])


def _get_worksheet(portfolio: str = DEFAULT_PORTFOLIO):
    """포트폴리오 이름으로 워크시트 반환. 없으면 새로 생성."""
    import gspread
    sh = _spreadsheet()
    try:
        ws = sh.worksheet(portfolio)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=portfolio, rows=500, cols=10)
        ws.update(values=[HEADER], range_name="A1")
        return ws

    # 헤더 없으면 초기화
    if ws.row_values(1) != HEADER:
        ws.update(values=[HEADER], range_name="A1")
    return ws


# ── 포트폴리오(계좌) 관리 ─────────────────────────────────────────────────

def list_portfolios() -> list[str]:
    """스프레드시트 탭 이름 목록 반환. 없으면 기본 계좌 생성."""
    sh = _spreadsheet()
    titles = [ws.title for ws in sh.worksheets()]
    if not titles:
        _get_worksheet(DEFAULT_PORTFOLIO)
        return [DEFAULT_PORTFOLIO]
    return titles


def add_portfolio(name: str) -> None:
    """새 계좌(탭) 추가."""
    import gspread
    sh = _spreadsheet()
    try:
        sh.worksheet(name)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=name, rows=500, cols=10)
        ws.update(values=[HEADER], range_name="A1")


def rename_portfolio(old_name: str, new_name: str) -> None:
    """계좌 이름 변경."""
    sh = _spreadsheet()
    ws = sh.worksheet(old_name)
    ws.update_title(new_name)


def delete_portfolio(name: str) -> None:
    """계좌(탭) 삭제. 마지막 계좌면 삭제 불가."""
    sh = _spreadsheet()
    if len(sh.worksheets()) <= 1:
        raise ValueError("마지막 계좌는 삭제할 수 없습니다.")
    ws = sh.worksheet(name)
    sh.del_worksheet(ws)


# ── 포지션 CRUD ───────────────────────────────────────────────────────────

def load_positions(portfolio: str = DEFAULT_PORTFOLIO) -> list[Position]:
    """해당 계좌의 포지션 목록 로드."""
    ws = _get_worksheet(portfolio)
    records = ws.get_all_records()
    positions = []
    for r in records:
        try:
            positions.append(Position(
                code=str(r["code"]).zfill(6),
                name=str(r["name"]),
                buy_price=float(r["buy_price"]),
                quantity=int(r["quantity"]),
                buy_date=str(r["buy_date"]),
            ))
        except (KeyError, ValueError):
            continue
    return positions


def add_position(pos: Position, portfolio: str = DEFAULT_PORTFOLIO) -> None:
    """포지션 추가 (같은 종목 코드가 있으면 덮어씀)."""
    ws = _get_worksheet(portfolio)
    _delete_by_code(ws, pos.code)
    ws.append_row([pos.code, pos.name, pos.buy_price, pos.quantity, pos.buy_date])


def remove_position(code: str, portfolio: str = DEFAULT_PORTFOLIO) -> bool:
    """종목코드로 포지션 삭제. 성공 여부 반환."""
    ws = _get_worksheet(portfolio)
    return _delete_by_code(ws, code.zfill(6))


def _delete_by_code(ws, code: str) -> bool:
    col = ws.col_values(1)
    for i, val in enumerate(col[1:], start=2):
        if str(val).zfill(6) == code.zfill(6):
            ws.delete_rows(i)
            return True
    return False


# ── ATR 계산 ──────────────────────────────────────────────────────────────

def _atr(df: pd.DataFrame, period: int = 14) -> float:
    hi, lo, cl = df["high"], df["low"], df["close"]
    prev_cl = cl.shift(1)
    tr = pd.concat([
        hi - lo,
        (hi - prev_cl).abs(),
        (lo - prev_cl).abs(),
    ], axis=1).max(axis=1)
    return float(tr.rolling(period).mean().iloc[-1])


# ── 신호 계산 ─────────────────────────────────────────────────────────────

def calc_signal(pos: Position) -> PositionSignal:
    """포지션 한 건의 신호 계산."""
    from . import technical_agent, backtest

    sig = PositionSignal(
        code=pos.code, name=pos.name,
        buy_price=pos.buy_price, quantity=pos.quantity, buy_date=pos.buy_date,
    )

    try:
        df = technical_agent.fetch_daily_prices_fast(pos.code, days=60)
        if len(df) < 20:
            sig.signal = "오류"
            sig.error = "시세 데이터 부족"
            return sig

        current_price = float(df["close"].iloc[-1])
        atr = _atr(df)

        sig.current_price = current_price
        sig.atr           = atr
        sig.stop_loss     = round(pos.buy_price - 1.5 * atr)
        sig.target1       = round(pos.buy_price + 2.0 * atr)
        sig.target2       = round(pos.buy_price + 3.0 * atr)
        sig.return_pct    = (current_price - pos.buy_price) / pos.buy_price * 100
        sig.profit_loss   = (current_price - pos.buy_price) * pos.quantity
        sig.eval_amount   = current_price * pos.quantity

        enriched = technical_agent.enrich(df.copy())
        raw_score = backtest.score_series(enriched).iloc[-1]
        # score_series 반환값은 -1~+1. ×100 해서 정수 표현
        sig.strategy_score = int(raw_score * 100) if not pd.isna(raw_score) else 0

    except Exception as e:
        sig.signal = "오류"
        sig.error  = str(e)
        return sig

    # ── 신호 판단 ─────────────────────────────────────────────────────────
    # strategy_score: 강세 ≥ 30, 중립 -10~30, 약세 < -10, 하락 < -30
    p, bp, sl, t1, t2 = (
        sig.current_price, sig.buy_price,
        sig.stop_loss, sig.target1, sig.target2,
    )
    sc = sig.strategy_score
    rp = sig.return_pct

    if p <= sl:
        # ATR 손절: 명확한 추세 이탈
        sig.signal        = "손절"
        sig.signal_reason = f"현재가({p:,.0f})가 손절선({sl:,.0f}) 하회 — ATR 기준 추세 이탈"

    elif p >= t2 and sc < 10:
        # 2차 목표가 도달 + 모멘텀 중립 이하 → 익절
        sig.signal        = "익절"
        sig.signal_reason = f"2차 목표가({t2:,.0f}) 도달, 기술점수 {sc:+d} 모멘텀 소멸"

    elif p >= t1 and sc < -10:
        # 1차 목표가 도달 + 점수가 약세권 → 익절
        sig.signal        = "익절"
        sig.signal_reason = f"1차 목표가({t1:,.0f}) 도달, 기술점수 {sc:+d} 하락 전환"

    elif rp >= 8 and sc < -25:
        # 수익 확보됐는데 기술 점수가 명확히 하락 전환
        sig.signal        = "익절 고려"
        sig.signal_reason = f"수익률 {rp:.1f}%, 기술점수 {sc:+d} 약세 — 일부 익절 고려"

    else:
        sig.signal = "보유"
        if p <= sl * 1.05:
            # 손절선 근처 경고
            sig.signal_reason = f"손절선({sl:,.0f}) 근접 주의 — 기술점수 {sc:+d}"
        elif rp < 0:
            sig.signal_reason = f"손절선 위 보유 유지 — 수익률 {rp:+.1f}%, 기술점수 {sc:+d}"
        elif sc >= 30:
            sig.signal_reason = f"기술점수 {sc:+d} 강세 — 상승 추세 유지"
        else:
            sig.signal_reason = f"수익률 {rp:+.1f}%, 기술점수 {sc:+d} 중립 — 손절선 위 보유"

    return sig


def calc_all_signals(positions: list[Position]) -> list[PositionSignal]:
    return [calc_signal(p) for p in positions]

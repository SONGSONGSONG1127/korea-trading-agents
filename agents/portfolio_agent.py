# -*- coding: utf-8 -*-
"""
포트폴리오 관리 에이전트

Google Sheets에 매수 포지션을 저장하고, 일일 종가 기준으로
ATR 손절 + 전략 점수 복합 신호(손절/보유/익절)를 계산한다.

시트 컬럼 (1행 = 헤더):
    A:code  B:name  C:buy_price  D:quantity  E:buy_date
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]
HEADER = ["code", "name", "buy_price", "quantity", "buy_date"]


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
    target1: float = 0.0       # ATR × 2.0
    target2: float = 0.0       # ATR × 3.0 (2차 익절)

    return_pct: float = 0.0    # %
    profit_loss: float = 0.0   # 원
    eval_amount: float = 0.0   # 평가금액

    strategy_score: int = 0
    signal: str = "확인중"     # 손절 / 익절 / 보유 / 오류
    signal_reason: str = ""
    error: str = ""


# ── Google Sheets 연결 ────────────────────────────────────────────────────

def _get_worksheet():
    """Streamlit secrets 기반 gspread 워크시트 반환."""
    import gspread
    from google.oauth2.service_account import Credentials
    import streamlit as st

    creds = Credentials.from_service_account_info(
        dict(st.secrets["gcp_service_account"]),
        scopes=SCOPES,
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(st.secrets["GSHEET_ID"])
    ws = sh.sheet1

    # 헤더 없으면 초기화
    if ws.row_values(1) != HEADER:
        ws.update(values=[HEADER], range_name="A1")
    return ws


def load_positions() -> list[Position]:
    """시트에서 포지션 목록 로드."""
    ws = _get_worksheet()
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


def add_position(pos: Position) -> None:
    """시트에 포지션 추가 (중복 코드면 기존 삭제 후 추가)."""
    ws = _get_worksheet()
    # 기존 동일 종목 삭제
    _delete_by_code(ws, pos.code)
    ws.append_row([pos.code, pos.name, pos.buy_price, pos.quantity, pos.buy_date])


def remove_position(code: str) -> bool:
    """종목코드로 포지션 삭제. 성공 여부 반환."""
    ws = _get_worksheet()
    return _delete_by_code(ws, code.zfill(6))


def _delete_by_code(ws, code: str) -> bool:
    col = ws.col_values(1)   # A열 전체 (코드)
    for i, val in enumerate(col[1:], start=2):   # 헤더(1행) 제외
        if str(val).zfill(6) == code.zfill(6):
            ws.delete_rows(i)
            return True
    return False


# ── 신호 계산 ─────────────────────────────────────────────────────────────

def _atr(df: pd.DataFrame, period: int = 14) -> float:
    """일봉 DataFrame에서 ATR 계산."""
    hi, lo, cl = df["high"], df["low"], df["close"]
    prev_cl = cl.shift(1)
    tr = pd.concat([
        hi - lo,
        (hi - prev_cl).abs(),
        (lo - prev_cl).abs(),
    ], axis=1).max(axis=1)
    return float(tr.rolling(period).mean().iloc[-1])


def calc_signal(pos: Position) -> PositionSignal:
    """포지션 한 건의 일일 신호 계산."""
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

        # 가격 지표
        sig.current_price = current_price
        sig.atr           = atr
        sig.stop_loss     = round(pos.buy_price - 1.5 * atr)
        sig.target1       = round(pos.buy_price + 2.0 * atr)
        sig.target2       = round(pos.buy_price + 3.0 * atr)
        sig.return_pct    = (current_price - pos.buy_price) / pos.buy_price * 100
        sig.profit_loss   = (current_price - pos.buy_price) * pos.quantity
        sig.eval_amount   = current_price * pos.quantity

        # 전략 점수 (기술만 — 빠른 계산)
        enriched = technical_agent.enrich(df.copy())
        score = backtest.score_series(enriched).iloc[-1]
        sig.strategy_score = int(score * 100) if not pd.isna(score) else 50

    except Exception as e:
        sig.signal = "오류"
        sig.error = str(e)
        return sig

    # ── 신호 판단 (ATR + 전략점수 복합) ─────────────────────────────────────
    p, bp, sl, t1, t2 = (
        sig.current_price, sig.buy_price,
        sig.stop_loss, sig.target1, sig.target2,
    )
    sc = sig.strategy_score
    rp = sig.return_pct

    if p <= sl:
        sig.signal        = "손절"
        sig.signal_reason = f"현재가({p:,.0f})가 손절선({sl:,.0f}) 하회"
    elif p >= t2 and sc < 60:
        sig.signal        = "익절"
        sig.signal_reason = f"2차 목표가({t2:,.0f}) 도달 + 전략점수 {sc}점 약화"
    elif p >= t1 and sc < 50:
        sig.signal        = "익절"
        sig.signal_reason = f"1차 목표가({t1:,.0f}) 도달 + 전략점수 {sc}점 모멘텀 소멸"
    elif rp >= 8 and sc < 45:
        sig.signal        = "익절 고려"
        sig.signal_reason = f"수익률 {rp:.1f}% + 전략점수 {sc}점 하락 반전 경고"
    elif p >= t1 and sc >= 65:
        sig.signal        = "보유"
        sig.signal_reason = f"목표가 초과지만 전략점수 {sc}점 강세 — 추가 상승 여력"
    else:
        sig.signal        = "보유"
        if rp < 0:
            sig.signal_reason = f"손절선 위 ({p:,.0f} > {sl:,.0f}) 보유 유지"
        else:
            sig.signal_reason = f"수익률 {rp:+.1f}% · 전략점수 {sc}점 정상"

    return sig


def calc_all_signals(positions: list[Position]) -> list[PositionSignal]:
    """전체 포지션 신호 일괄 계산."""
    return [calc_signal(p) for p in positions]

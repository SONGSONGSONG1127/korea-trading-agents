# -*- coding: utf-8 -*-
"""
Agent 2: 기술적 분석 에이전트 (v2)

네이버 증권 일별 시세(약 250거래일)를 수집해 지표를 계산하고,
모든 판단을 Signal(증거 객체)로 저장한 뒤
카테고리(추세/모멘텀/거래량/위치·변동성)별 점수 + 합의(confluence) 배수로
기술 점수를 산출한다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from io import StringIO

import numpy as np
import pandas as pd
import requests

from .models import Signal

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Referer": "https://finance.naver.com/",
}

CATEGORY_WEIGHTS = {"추세": 0.35, "모멘텀": 0.25, "거래량": 0.25, "위치/변동성": 0.15}


@dataclass
class TechnicalReport:
    code: str
    df: pd.DataFrame | None = None
    price: float = 0.0
    ma5: float = 0.0
    ma20: float = 0.0
    ma60: float = 0.0
    rsi: float = 0.0
    atr: float = 0.0
    atr_pct: float = 0.0
    rvol: float = 0.0
    signals: list[Signal] = field(default_factory=list)
    cat_scores: dict[str, float] = field(default_factory=dict)
    confluence_mult: float = 1.0
    flags: list[str] = field(default_factory=list)
    score: float = 0.0                  # -1.0 ~ +1.0
    regime: str | None = None           # 순풍 | 역풍 | 중립
    regime_evidence: str = ""
    backtest: dict | None = None
    summary: str = ""
    error: str | None = None


# ── 데이터 수집 ──────────────────────────────────────────────────────────

def fetch_daily_prices_fast(code: str, days: int = 260) -> pd.DataFrame:
    """네이버 차트 JSON API — 요청 1번으로 전체 일봉을 받는다."""
    import ast
    from datetime import datetime, timedelta

    end = datetime.now()
    start = end - timedelta(days=int(days * 1.6) + 30)
    res = requests.get(
        "https://api.finance.naver.com/siseJson.naver",
        params={
            "symbol": code, "requestType": "1",
            "startTime": start.strftime("%Y%m%d"), "endTime": end.strftime("%Y%m%d"),
            "timeframe": "day",
        },
        headers=HEADERS, timeout=10,
    )
    res.raise_for_status()
    data = ast.literal_eval(res.text.replace("\n", "").replace("\t", "").strip())
    df = pd.DataFrame(data[1:], columns=data[0]).rename(
        columns={
            "날짜": "date", "시가": "open", "고가": "high",
            "저가": "low", "종가": "close", "거래량": "volume",
        }
    )[["date", "open", "high", "low", "close", "volume"]]
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["close"]).fillna(0)
    return df.drop_duplicates(subset="date").sort_values("date").reset_index(drop=True)


def fetch_daily_prices(code: str, pages: int = 26) -> pd.DataFrame:
    """일봉 수집. JSON API 우선, 실패 시 일별 시세 페이지 크롤링으로 폴백."""
    try:
        df = fetch_daily_prices_fast(code, days=pages * 10)
        if len(df) >= 60:
            return df
    except Exception:
        pass

    frames = []
    for page in range(1, pages + 1):
        url = f"https://finance.naver.com/item/sise_day.naver?code={code}&page={page}"
        res = requests.get(url, headers=HEADERS, timeout=10)
        res.raise_for_status()
        res.encoding = "euc-kr"
        tables = pd.read_html(StringIO(res.text))
        if not tables:
            break
        frames.append(tables[0])

    df = pd.concat(frames, ignore_index=True).dropna()
    df = df.rename(
        columns={
            "날짜": "date", "종가": "close", "시가": "open",
            "고가": "high", "저가": "low", "거래량": "volume",
        }
    )[["date", "open", "high", "low", "close", "volume"]]
    df["date"] = pd.to_datetime(df["date"], format="%Y.%m.%d")
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col])
    return df.drop_duplicates(subset="date").sort_values("date").reset_index(drop=True)


def fetch_market_regime(index_code: str = "KOSPI") -> tuple[str, str]:
    """KOSPI 지수 vs 60일선으로 시장 레짐(순풍/역풍/중립)을 판정한다."""
    frames = []
    for page in range(1, 15):
        url = f"https://finance.naver.com/sise/sise_index_day.naver?code={index_code}&page={page}"
        res = requests.get(url, headers=HEADERS, timeout=10)
        res.raise_for_status()
        res.encoding = "euc-kr"
        tables = pd.read_html(StringIO(res.text))
        if not tables:
            break
        frames.append(tables[0])
    idx = pd.concat(frames, ignore_index=True).dropna(subset=["체결가"])
    close = pd.to_numeric(idx["체결가"]).iloc[::-1].reset_index(drop=True)  # 오름차순
    if len(close) < 60:
        return "중립", "지수 데이터 부족"
    ma60 = close.rolling(60).mean().iloc[-1]
    last = close.iloc[-1]
    if last > ma60 * 1.005:
        return "순풍", f"{index_code} {last:,.0f} > 60일선 {ma60:,.0f} (시장 상승 국면)"
    if last < ma60 * 0.995:
        return "역풍", f"{index_code} {last:,.0f} < 60일선 {ma60:,.0f} (시장 하락 국면)"
    return "중립", f"{index_code} {last:,.0f} ≈ 60일선 {ma60:,.0f}"


# ── 지표 계산 ────────────────────────────────────────────────────────────

def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))


def norm_slope(s: pd.Series, n: int = 20) -> float:
    """정규화 기울기 (다이버전스 판정용)."""
    y = s.tail(n).values.astype(float)
    if len(y) < n:
        return 0.0
    return float(np.polyfit(np.arange(n), y, 1)[0] / (abs(y.mean()) + 1e-9))


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    """모든 지표 컬럼을 추가한다."""
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]

    df["ma5"] = c.rolling(5).mean()
    df["ma20"] = c.rolling(20).mean()
    df["ma60"] = c.rolling(60).mean()
    df["rsi"] = compute_rsi(c)

    # MACD (12, 26, 9)
    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    df["macd"] = ema12 - ema26
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]

    # 볼린저밴드 (20, 2)
    sd = c.rolling(20).std(ddof=0)
    df["bb_upper"] = df["ma20"] + 2 * sd
    df["bb_lower"] = df["ma20"] - 2 * sd
    df["pct_b"] = (c - df["bb_lower"]) / (df["bb_upper"] - df["bb_lower"] + 1e-9)
    df["bandwidth"] = (df["bb_upper"] - df["bb_lower"]) / df["ma20"]

    # ATR (14, Wilder)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    df["atr"] = tr.ewm(alpha=1 / 14, adjust=False).mean()

    # 거래량: 상대거래량 + OBV
    df["vol_ma20"] = v.rolling(20).mean()
    df["rvol"] = v / (df["vol_ma20"] + 1e-9)
    df["obv"] = (np.sign(c.diff()).fillna(0) * v).cumsum()
    df["obv_ma20"] = df["obv"].rolling(20).mean()

    df["ma20_slope"] = df["ma20"] / df["ma20"].shift(5) - 1  # 5일간 변화율
    return df


# ── 신호 생성 (증거 객체) ────────────────────────────────────────────────

def _trend_signals(df: pd.DataFrame, vol_dead: bool) -> list[Signal]:
    sig: list[Signal] = []
    last = df.iloc[-1]
    price, ma5, ma20, ma60 = last["close"], last["ma5"], last["ma20"], last["ma60"]
    slope = last["ma20_slope"]
    rvol = last["rvol"]

    # 가격 vs MA20 — MA20이 하락 기울기면 감액 (하락 추세 속 일시 반등 오판 방지)
    if price > ma20:
        s = 0.1 if slope < 0 else 0.3
        ev = f"현재가 {price:,.0f}원이 20일선({ma20:,.0f}원) 위"
        ev += " (단, 20일선 자체는 하락 중이라 감액)" if slope < 0 else ""
        sig.append(Signal("가격 vs 20일선", "추세", s, ev))
    else:
        sig.append(Signal("가격 vs 20일선", "추세", -0.3,
                          f"현재가 {price:,.0f}원이 20일선({ma20:,.0f}원) 아래"))

    sig.append(Signal("MA5 vs MA20", "추세", 0.15 if ma5 > ma20 else -0.15,
                      f"5일선({ma5:,.0f}) {'>' if ma5 > ma20 else '<'} 20일선({ma20:,.0f})"))

    if pd.notna(ma60):
        if ma5 > ma20 > ma60:
            sig.append(Signal("이평선 배열", "추세", 0.15, "정배열 (MA5 > MA20 > MA60)"))
        elif ma5 < ma20 < ma60:
            sig.append(Signal("이평선 배열", "추세", -0.15, "역배열 (MA5 < MA20 < MA60)"))

    if slope > 0.005:
        sig.append(Signal("MA20 기울기", "추세", 0.15, f"20일선이 5일 전 대비 {slope:+.1%} 상승 기울기"))
    elif slope < -0.005:
        sig.append(Signal("MA20 기울기", "추세", -0.15, f"20일선이 5일 전 대비 {slope:+.1%} 하락 기울기"))
    else:
        sig.append(Signal("MA20 기울기", "추세", 0.0, f"20일선 횡보 ({slope:+.1%})"))

    sig.append(Signal("MACD 0선", "추세", 0.10 if last["macd"] > 0 else -0.10,
                      f"MACD {'0선 위 (중기 상승 국면)' if last['macd'] > 0 else '0선 아래 (중기 하락 국면)'}"))

    # MA5/20 골든/데드크로스 (최근 3일) — 거래량 확인 게이트
    diff = (df["ma5"] - df["ma20"]).tail(4).values
    gate = 1.0 if (rvol >= 1.5 and not vol_dead) else 0.5
    if diff[0] < 0 < diff[-1]:
        note = "" if gate == 1.0 else " — 거래량 확인 없음, 신뢰 50%"
        sig.append(Signal("MA 골든크로스", "추세", 0.20 * gate,
                          f"최근 3일 내 5일선이 20일선 상향 돌파{note}"))
    elif diff[0] > 0 > diff[-1]:
        sig.append(Signal("MA 데드크로스", "추세", -0.20,
                          "최근 3일 내 5일선이 20일선 하향 돌파"))
    return sig


def _volume_signals(df: pd.DataFrame) -> tuple[list[Signal], bool]:
    sig: list[Signal] = []
    last = df.iloc[-1]
    rvol = last["rvol"]
    up_candle = last["close"] > last["open"]

    if rvol >= 2.0:
        s = 0.30 if up_candle else -0.30
        sig.append(Signal("상대거래량", "거래량", s,
                          f"금일 거래량이 20일 평균의 {rvol:.1f}배, "
                          f"{'양봉 동반 (매집성)' if up_candle else '음봉 동반 (매물 출회)'}"))
    elif rvol >= 1.5:
        s = 0.15 if up_candle else -0.15
        sig.append(Signal("상대거래량", "거래량", s,
                          f"거래량 20일 평균의 {rvol:.1f}배 ({'양봉' if up_candle else '음봉'})"))
    else:
        sig.append(Signal("상대거래량", "거래량", 0.0, f"거래량 평이 (20일 평균의 {rvol:.1f}배)"))

    obv_above = last["obv"] > last["obv_ma20"]
    sig.append(Signal("OBV 추세", "거래량", 0.15 if obv_above else -0.15,
                      f"OBV가 20일 평균 {'위 (누적 매수 우위)' if obv_above else '아래 (누적 매도 우위)'}"))

    # 가격-OBV 다이버전스
    c = df["close"]
    obv_slope = norm_slope(df["obv"])
    if c.iloc[-1] >= c.tail(20).max() and obv_slope < 0:
        sig.append(Signal("OBV 다이버전스", "거래량", -0.20,
                          "종가는 20일 신고가인데 OBV는 하락 — 상승에 수급이 따라붙지 않음 (경고)"))
    elif c.iloc[-1] <= c.tail(20).min() and obv_slope > 0:
        sig.append(Signal("OBV 다이버전스", "거래량", 0.20,
                          "종가는 20일 신저가인데 OBV는 상승 — 저점 매집 흔적"))

    vol_dead = bool((df["rvol"].tail(5) < 0.7).all())
    return sig, vol_dead


def _momentum_signals(df: pd.DataFrame, trend_score: float) -> list[Signal]:
    sig: list[Signal] = []
    last = df.iloc[-1]
    rsi = last["rsi"]

    # 문맥형 RSI: 강추세에서의 과열/침체는 정상으로 본다
    if rsi >= 70:
        s = -0.1 if trend_score >= 0.5 else -0.3
        ctx = "강한 상승 추세 중이라 정상적 과열로 판단, 소폭 감점" if trend_score >= 0.5 else "추세 뒷받침 없는 과열"
        sig.append(Signal("RSI(14)", "모멘텀", s, f"RSI {rsi:.1f} 과매수 — {ctx}"))
    elif rsi <= 30:
        s = 0.1 if trend_score <= -0.5 else 0.3
        ctx = "강한 하락 추세라 침체 지속 가능, 소폭 가점" if trend_score <= -0.5 else "과매도 — 기술적 반등 여지"
        sig.append(Signal("RSI(14)", "모멘텀", s, f"RSI {rsi:.1f} — {ctx}"))
    else:
        sig.append(Signal("RSI(14)", "모멘텀", 0.0, f"RSI {rsi:.1f} 중립 구간"))

    # MACD 시그널 교차 (최근 3일)
    macd, signal = df["macd"], df["macd_signal"]
    cross_up = (macd > signal) & (macd.shift() <= signal.shift())
    cross_dn = (macd < signal) & (macd.shift() >= signal.shift())
    if cross_up.tail(3).any():
        below_zero = macd.iloc[-1] < 0
        s = 0.20 if below_zero else 0.10
        sig.append(Signal("MACD 크로스", "모멘텀", s,
                          "최근 3일 내 MACD 골든크로스"
                          + (" — 0선 아래 발생 (저점 반전형)" if below_zero else " (0선 위, 추세 지속형)")))
    elif cross_dn.tail(3).any():
        above_zero = macd.iloc[-1] > 0
        s = -0.20 if above_zero else -0.10
        sig.append(Signal("MACD 크로스", "모멘텀", s, "최근 3일 내 MACD 데드크로스"))

    hist_diff = df["macd_hist"].diff().tail(3)
    if (hist_diff > 0).all():
        sig.append(Signal("MACD 히스토그램", "모멘텀", 0.15, "히스토그램 3일 연속 증가 — 모멘텀 가속 중"))
    elif (hist_diff < 0).all():
        sig.append(Signal("MACD 히스토그램", "모멘텀", -0.15, "히스토그램 3일 연속 감소 — 모멘텀 둔화 중"))
    return sig


def _position_signals(df: pd.DataFrame) -> tuple[list[Signal], bool]:
    sig: list[Signal] = []
    last = df.iloc[-1]
    pct_b, rvol, slope = last["pct_b"], last["rvol"], last["ma20_slope"]

    if slope > 0 and 0.1 <= pct_b <= 0.35:
        sig.append(Signal("볼린저 %B", "위치/변동성", 0.20,
                          f"%B {pct_b:.2f} — 상승 추세 내 밴드 하단 눌림목 구간"))
    elif pct_b > 1.0 and rvol < 1.5:
        sig.append(Signal("볼린저 %B", "위치/변동성", -0.20,
                          f"%B {pct_b:.2f} — 거래량 없이 상단 밴드 이탈 (과열)"))
    elif pct_b > 0.8 and rvol >= 2.0:
        sig.append(Signal("볼린저 %B", "위치/변동성", 0.15,
                          f"%B {pct_b:.2f} + 거래량 {rvol:.1f}배 — 밴드 타기(돌파 지속형)"))
    else:
        sig.append(Signal("볼린저 %B", "위치/변동성", 0.0,
                          f"%B {pct_b:.2f} — 정상 변동 범위 내"))

    bw = df["bandwidth"].dropna()
    squeeze = len(bw) >= 60 and bw.iloc[-1] <= bw.rolling(60).min().iloc[-1] * 1.05
    return sig, bool(squeeze)


# ── 메인 ────────────────────────────────────────────────────────────────

def run(code: str, df: pd.DataFrame | None = None,
        regime: tuple[str, str] | None = None) -> TechnicalReport:
    """df/regime을 미리 넘기면 재수집 없이 사용한다 (스크리너에서 재활용)."""
    code = re.sub(r"\D", "", code).zfill(6)
    report = TechnicalReport(code=code)
    if df is not None:
        df = df.copy()
    else:
        try:
            df = fetch_daily_prices(code)
        except Exception as e:
            report.error = f"시세 수집 실패: {e}"
            return report

    # 거래정지 더미 행 제거 (네이버는 정지 기간을 거래량 0 + 시가/고가/저가 0으로 표시)
    halt = (df["volume"] == 0) & (df["open"] == 0)
    if halt.any():
        df = df[~halt].reset_index(drop=True)

    # 가격 불연속 방어: 가격제한폭(±30%)을 넘는 점프는 액면분할·감자 등 자본 변경 —
    # 분석을 포기하지 않고 불연속 '이후' 구간만 사용한다.
    jumps = df["close"].pct_change().abs()
    if (jumps > 0.31).any():
        cut = jumps[jumps > 0.31].index[-1]
        ratio = df["close"].iloc[cut - 1] / df["close"].iloc[cut]
        cut_date = df["date"].iloc[cut].date()
        df = df.iloc[cut:].reset_index(drop=True)
        report.flags.append(
            f"가격 불연속 감지 ({cut_date}, 약 1:{ratio:.1f} — 액면분할/병합 추정) → "
            f"이후 {len(df)}거래일만 사용"
        )

    if len(df) < 60:
        report.error = "시세 데이터가 부족합니다 (유효 구간 60거래일 미만)."
        return report

    df = enrich(df)
    last = df.iloc[-1]
    report.df = df
    report.price = float(last["close"])
    report.ma5 = float(last["ma5"])
    report.ma20 = float(last["ma20"])
    report.ma60 = float(last["ma60"]) if pd.notna(last["ma60"]) else 0.0
    report.rsi = float(last["rsi"])
    report.atr = float(last["atr"])
    report.atr_pct = report.atr / report.price
    report.rvol = float(last["rvol"])

    # 신호 생성 (거래량 → 추세 → 모멘텀 → 위치 순: 게이트/문맥 의존성 때문)
    vol_sigs, vol_dead = _volume_signals(df)
    trend_sigs = _trend_signals(df, vol_dead)
    cat = {name: 0.0 for name in CATEGORY_WEIGHTS}
    cat["추세"] = float(np.clip(sum(s.score for s in trend_sigs), -1, 1))
    momentum_sigs = _momentum_signals(df, cat["추세"])
    pos_sigs, squeeze = _position_signals(df)

    report.signals = trend_sigs + momentum_sigs + vol_sigs + pos_sigs
    cat["모멘텀"] = float(np.clip(sum(s.score for s in momentum_sigs), -1, 1))
    cat["거래량"] = float(np.clip(sum(s.score for s in vol_sigs), -1, 1))
    cat["위치/변동성"] = float(np.clip(sum(s.score for s in pos_sigs), -1, 1))
    report.cat_scores = {k: round(v, 3) for k, v in cat.items()}

    if vol_dead:
        report.flags.append("관심 소멸 — 5일 연속 거래량이 평균의 70% 미만, 돌파 신호 신뢰 하향")
    if squeeze:
        report.flags.append("변동성 압축(스퀴즈) — 밴드폭이 60일 최저 수준, 방향 결정 임박")

    # 합의(confluence) 배수: |점수| 0.2 미만 카테고리는 기권
    votes = [np.sign(s) for s in cat.values() if abs(s) >= 0.2]
    agree = max(votes.count(1), votes.count(-1)) if votes else 0
    conflict = min(votes.count(1), votes.count(-1)) if votes else 0
    if agree >= 3 and conflict == 0:
        report.confluence_mult = 1.25
    elif conflict >= 2:
        report.confluence_mult = 0.60
        report.flags.append("카테고리 간 신호 상충 — 확신도 하향")

    base = sum(CATEGORY_WEIGHTS[k] * v for k, v in cat.items())
    report.score = round(float(np.clip(base * report.confluence_mult, -1, 1)), 3)

    # 시장 레짐 (실패해도 분석은 계속)
    if regime is not None:
        report.regime, report.regime_evidence = regime
    else:
        try:
            report.regime, report.regime_evidence = fetch_market_regime("KOSPI")
        except Exception:
            report.regime = None

    # 간이 백테스트
    try:
        from . import backtest
        report.backtest = backtest.run(df)
    except Exception:
        report.backtest = None

    up = sum(1 for s in cat.values() if s >= 0.2)
    dn = sum(1 for s in cat.values() if s <= -0.2)
    report.summary = (
        f"현재가 {report.price:,.0f}원, RSI {report.rsi:.1f}, ATR {report.atr:,.0f}원({report.atr_pct:.1%}). "
        f"카테고리 4축 중 {up}축 상방 / {dn}축 하방, 합의 배수 ×{report.confluence_mult:.2f} → "
        f"기술 점수 {report.score:+.2f}."
    )
    return report

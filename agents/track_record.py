# -*- coding: utf-8 -*-
"""
트랙레코드 — 매일 자동 기록된 스크리너 top 종목의 실제 성과 채점

daily_report.py가 _트랙레코드 시트에 쌓는 (날짜, 시장, 순위, 코드, 종목, 점수, 종가)를
읽어, 각 픽의 추천일 이후 실제 수익률과 벤치마크(KOSPI/S&P500) 대비 초과수익을 계산한다.
→ "이 스크리너를 믿을 근거가 데이터로 쌓이고 있는가"에 대한 답.
"""
from __future__ import annotations

import time
from typing import Callable

import numpy as np
import pandas as pd

ProgressCb = Callable[[int, int, str], None] | None

TRACK_SHEET = "_트랙레코드"


def load_records() -> list[dict]:
    """트랙레코드 시트 전체 로드."""
    import gspread
    from .portfolio_agent import _spreadsheet
    sh = _spreadsheet()
    try:
        ws = sh.worksheet(TRACK_SHEET)
    except gspread.WorksheetNotFound:
        return []
    return ws.get_all_records()


def evaluate(market: str = "KR", progress: ProgressCb = None) -> dict:
    """market별 픽 성과 채점. {rows, summary, n_records}"""
    from . import technical_agent, us_data

    recs = [r for r in load_records() if str(r.get("시장")) == market]
    if not recs:
        return {"rows": [], "summary": None, "n_records": 0}

    # 벤치마크 시계열
    try:
        if market == "US":
            bench = us_data.fetch_benchmark(days=800)
        else:
            bench = technical_agent.fetch_daily_prices_fast("KOSPI", days=800)
        bench_s = bench.set_index("date")["close"]
        bench_last = float(bench_s.iloc[-1])
    except Exception:
        bench_s, bench_last = None, None

    # 종목별 가격 로드 (중복 제거)
    def _code(r) -> str:
        c = str(r.get("코드", "")).strip()
        return c.zfill(6) if market == "KR" and c.isdigit() else c.upper()

    codes = sorted({_code(r) for r in recs if _code(r)})
    prices: dict[str, pd.Series] = {}
    if market == "US":
        fetched = us_data.fetch_many(codes, days=800, progress=progress)
        prices = {c: df.set_index("date")["close"] for c, df in fetched.items()}
    else:
        for i, c in enumerate(codes, 1):
            if progress:
                progress(i, len(codes), c)
            try:
                df = technical_agent.fetch_daily_prices_fast(c, days=800)
                df["date"] = pd.to_datetime(df["date"])
                prices[c] = df.set_index("date")["close"]
            except Exception:
                continue
            time.sleep(0.05)

    rows: list[dict] = []
    for r in recs:
        c = _code(r)
        s = prices.get(c)
        try:
            entry_px = float(r["종가"])
            d0 = pd.Timestamp(str(r["날짜"]))
        except (KeyError, ValueError):
            continue
        if s is None or entry_px <= 0:
            continue
        cur = float(s.iloc[-1])
        ret = cur / entry_px - 1
        excess = None
        if bench_s is not None:
            b0 = bench_s.asof(d0)
            if pd.notna(b0) and b0 > 0:
                excess = ret - (bench_last / float(b0) - 1)
        rows.append({
            "date":     str(r["날짜"]),
            "rank":     r.get("순위", ""),
            "code":     c,
            "name":     str(r.get("종목", c)),
            "score":    float(r.get("점수", 0) or 0),
            "entry":    entry_px,
            "current":  cur,
            "ret":      ret,
            "excess":   excess,
            "days":     max((pd.Timestamp(s.index[-1]) - d0).days, 0),
        })

    rows.sort(key=lambda x: (x["date"], x["rank"]), reverse=True)

    summary = None
    if rows:
        rets = np.array([x["ret"] for x in rows])
        exs = np.array([x["excess"] for x in rows if x["excess"] is not None])
        summary = {
            "n":          len(rows),
            "n_days":     len({x["date"] for x in rows}),
            "avg_ret":    float(rets.mean()),
            "win_rate":   float((rets > 0).mean()),
            "avg_excess": float(exs.mean()) if len(exs) else None,
            "excess_win": float((exs > 0).mean()) if len(exs) else None,
        }
    return {"rows": rows, "summary": summary, "n_records": len(recs)}

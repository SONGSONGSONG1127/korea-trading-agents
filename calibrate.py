# -*- coding: utf-8 -*-
"""
임계값 보정용 풀드 백테스트

유동성 상위 종목 전체의 과거 구간에 근사 기술 점수를 계산하고,
(점수 ≥ 임계) 신호의 10일 후 수익률을 시장 레짐(KOSPI 60일선 위/아래)별로 집계한다.
뉴스는 과거 데이터가 없으므로 제외 — "뉴스 확인용 전환"의 타당성 근거이기도 하다.
"""
import numpy as np
import pandas as pd

from agents import backtest, screener, technical_agent

HORIZON = 10


def clean(df: pd.DataFrame) -> pd.DataFrame:
    halt = (df["volume"] == 0) & (df["open"] == 0)
    df = df[~halt].reset_index(drop=True)
    jumps = df["close"].pct_change().abs()
    if (jumps > 0.31).any():
        df = df.iloc[jumps[jumps > 0.31].index[-1]:].reset_index(drop=True)
    return df


print("유동성 상위 종목 수집 중...")
leaders = screener._market_leaders()[:150]
print(f"{len(leaders)}종목")

kospi = technical_agent.fetch_daily_prices_fast("KOSPI", days=320)
kospi["ma60"] = kospi["close"].rolling(60).mean()
regime = kospi.set_index("date")["close"] > kospi.set_index("date")["ma60"]  # True = 강세

pool = []
for i, cand in enumerate(leaders, 1):
    try:
        df = clean(technical_agent.fetch_daily_prices_fast(cand.code, days=300))
        if len(df) < 90:
            continue
        df = technical_agent.enrich(df)
        s = backtest.score_series(df)
        fwd = df["close"].shift(-HORIZON) / df["close"] - 1
        part = pd.DataFrame({"date": df["date"], "score": s, "fwd": fwd})
        part = part[df["ma60"].notna() & s.notna() & fwd.notna()]
        pool.append(part)
    except Exception:
        continue
    if i % 50 == 0:
        print(f"  {i}/{len(leaders)}")

data = pd.concat(pool, ignore_index=True)
data["bull"] = data["date"].map(regime).fillna(False)
print(f"\n풀드 표본: {len(data):,} 종목-일 ({data['date'].min().date()} ~ {data['date'].max().date()}), "
      f"강세 레짐 {data['bull'].mean():.0%}")

qs = data["score"].quantile([0.5, 0.75, 0.9, 0.95, 0.99])
print("점수 분포 백분위:", ", ".join(f"p{int(q*100)}={v:+.2f}" for q, v in qs.items()))

base_all = data["fwd"]
print(f"\n기준선(전체 종목-일): 10일 평균 {base_all.mean():+.2%}, 승률 {(base_all > 0).mean():.0%}")

print(f"\n{'임계':>5} | {'--- 전체 ---':^28} | {'--- 강세(지수>60일선) ---':^28} | {'--- 약세(지수<60일선) ---':^28}")
print(f"{'':>5} | {'표본':>7} {'승률':>6} {'평균':>7} | {'표본':>7} {'승률':>6} {'평균':>7} | {'표본':>7} {'승률':>6} {'평균':>7}")
for thr in [0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40]:
    sig = data[data["score"] >= thr]
    line = f"{thr:>5.2f} | {len(sig):>7,} {(sig['fwd'] > 0).mean():>6.0%} {sig['fwd'].mean():>+7.2%}"
    for flag in [True, False]:
        sub = sig[sig["bull"] == flag]
        if len(sub):
            line += f" | {len(sub):>7,} {(sub['fwd'] > 0).mean():>6.0%} {sub['fwd'].mean():>+7.2%}"
        else:
            line += f" | {'-':>7} {'-':>6} {'-':>7}"
    print(line)

# 참고: 약세 레짐 전체 기준선 (신호 없이)
for flag, name in [(True, "강세"), (False, "약세")]:
    sub = data[data["bull"] == flag]
    print(f"{name} 레짐 기준선: 평균 {sub['fwd'].mean():+.2%}, 승률 {(sub['fwd'] > 0).mean():.0%} (표본 {len(sub):,})")

# ── 100점 점수용 보정 테이블 저장 ───────────────────────────────────────
# 점수(0~100) = 원점수가 과거 풀드 분포에서 차지하는 백분위.
# 구간(10점 단위)별 과거 10일 승률/평균 수익률도 저장해 화면에 근거로 표시한다.
import json
from pathlib import Path

quantiles = {str(p): round(float(data["score"].quantile(p / 100)), 4) for p in range(1, 100)}
data["pct"] = data["score"].rank(pct=True) * 100
bands = []
for lo in range(0, 100, 10):
    hi = lo + 10
    sub = data[(data["pct"] >= lo) & (data["pct"] < hi)] if hi < 100 else data[data["pct"] >= lo]
    bands.append({
        "lo": lo, "hi": hi, "n": int(len(sub)),
        "win": round(float((sub["fwd"] > 0).mean()), 3),
        "avg": round(float(sub["fwd"].mean()), 4),
    })
out = {
    "generated": str(data["date"].max().date()),
    "n_samples": int(len(data)),
    "n_stocks": len(pool),
    "horizon": HORIZON,
    "baseline_win": round(float((data["fwd"] > 0).mean()), 3),
    "baseline_avg": round(float(data["fwd"].mean()), 4),
    "quantiles": quantiles,
    "bands": bands,
}
path = Path(__file__).parent / "agents" / "calibration.json"
path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"\n보정 테이블 저장: {path} (표본 {out['n_samples']:,}, {out['n_stocks']}종목)")

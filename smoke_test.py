# -*- coding: utf-8 -*-
"""에이전트 3개가 실제 데이터로 동작하는지 확인하는 스모크 테스트."""
import sys

from agents import news_agent, strategy_agent, technical_agent

code = sys.argv[1] if len(sys.argv) > 1 else "005930"

news = news_agent.run(code)
print(f"[Agent 1] {news.stock_name}({news.code}) raw={news.raw_count} clusters={len(news.items)} "
      f"score={news.score} veto={news.veto} events={len(news.events)}")
print("  summary:", news.summary or news.error)
for it in news.items[:3]:
    print(f"  - [{it.kind}][{it.sentiment:+.1f}] {it.title} ({it.date}) x{it.dup_count}")

tech = technical_agent.run(code)
if tech.error:
    print("[Agent 2] ERROR:", tech.error)
    sys.exit(1)
print(f"[Agent 2] rows={len(tech.df)} price={tech.price:,.0f} rsi={tech.rsi:.1f} "
      f"atr={tech.atr:,.0f}({tech.atr_pct:.1%}) rvol={tech.rvol:.2f}")
print(f"  cats={tech.cat_scores} mult={tech.confluence_mult} score={tech.score}")
print(f"  regime={tech.regime} ({tech.regime_evidence})")
print(f"  flags={tech.flags}")
print(f"  backtest={tech.backtest}")
for s in tech.signals:
    print(f"  [{s.category}] {s.name} {s.score:+.2f} — {s.evidence}")

strat = strategy_agent.run(news, tech)
print(f"[Agent 3] opinion={strat.opinion} reason={strat.reason_code!r} total={strat.total_score}")
print(f"  {strat.confidence} | contrib={strat.contrib_pct}")
print(f"  buy={strat.buy_price:,.0f} stop={strat.stop_loss:,.0f} "
      f"t1={strat.target_price:,.0f} t2={strat.target2_price:,.0f} rr=1:{strat.rr}")
print(f"  entry_basis: {strat.entry_basis}")
print(f"  stop_basis : {strat.stop_basis}")
print(f"  conflicts  : {len(strat.conflicts)}")
print("---- 5단 근거 ----")
for k, v in strat.sections.items():
    print(f"[{k}]\n{v}\n")

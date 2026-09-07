# -*- coding: utf-8 -*-
"""
일일 브리핑 (GitHub Actions 크론용, 매일 아침 07:00 KST)

  1. 모든 포트폴리오 계좌의 신호 점검 → 손절/익절/익절 고려 발생 시 강조
  2. K 스크리너 top5 + USA 스크리너 top5 요약
  3. 트랙레코드: 오늘의 top 종목을 _트랙레코드 시트에 자동 기록 (성과 검증용)
  4. 텔레그램 발송

로컬 테스트: python scripts/daily_report.py --dry  (발송·기록 없이 출력만)
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

KST = timezone(timedelta(hours=9))

TRACK_SHEET = "_트랙레코드"
TRACK_HEADER = ["날짜", "시장", "순위", "코드", "종목", "점수", "종가"]


def portfolio_section() -> tuple[str, int]:
    """전 계좌 신호 요약. (텍스트, 경보 수)"""
    from agents import portfolio_agent
    lines, n_alerts = [], 0
    try:
        for pf in portfolio_agent.list_portfolios():
            positions = portfolio_agent.load_positions(pf)
            if not positions:
                continue
            sigs = portfolio_agent.calc_all_signals(positions)
            alerts = [s for s in sigs if s.signal in ("손절", "익절", "익절 고려")]
            total_inv = sum(s.buy_price * s.quantity for s in sigs)
            total_ev = sum(s.eval_amount for s in sigs)
            pl_pct = (total_ev / total_inv - 1) * 100 if total_inv else 0
            lines.append(f"*{pf}* — 수익률 {pl_pct:+.1f}% ({len(sigs)}종목)")
            for s in alerts:
                n_alerts += 1
                lines.append(f"  🚨 {s.signal}: {s.name} — {s.signal_reason}")
    except Exception as e:
        lines.append(f"(포트폴리오 점검 실패: {e})")
    return "\n".join(lines), n_alerts


def screener_section(track_rows: list) -> str:
    """K/USA 스크리너 top5 + 트랙레코드 행 축적."""
    today = datetime.now(KST).strftime("%Y-%m-%d")
    lines = []
    try:
        from agents import screener
        top = screener.stage1(liquidity_top=120)[:5]
        lines.append("*🇰🇷 오늘의 기술점수 top5*")
        for i, c in enumerate(top, 1):
            lines.append(f"  {i}. {c.name} ({c.code}) {c.quick_score:+.2f} · {c.close:,.0f}원")
            track_rows.append([today, "KR", i, c.code, c.name, c.quick_score, c.close])
    except Exception as e:
        lines.append(f"(K 스크리너 실패: {e})")
    try:
        from agents import us_screener
        res = us_screener.run(liquidity_top=100, n_top=5)
        lines.append("*🇺🇸 S&P500 기술점수 top5*")
        for i, r in enumerate(res["rows"], 1):
            lines.append(f"  {i}. {r['name']} ({r['code']}) {r['score']:+.2f} · ${r['close']:,.2f}")
            track_rows.append([today, "US", i, r["code"], r["name"], r["score"], r["close"]])
    except Exception as e:
        lines.append(f"(US 스크리너 실패: {e})")
    return "\n".join(lines)


def save_track(track_rows: list) -> bool:
    """트랙레코드 시트에 오늘의 top 종목 기록."""
    if not track_rows:
        return False
    try:
        import gspread
        from agents.portfolio_agent import _spreadsheet
        sh = _spreadsheet()
        try:
            ws = sh.worksheet(TRACK_SHEET)
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(title=TRACK_SHEET, rows=5000, cols=10)
            ws.update(values=[TRACK_HEADER], range_name="A1")
        ws.append_rows(track_rows)
        return True
    except Exception:
        return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="발송·기록 없이 출력만")
    args = ap.parse_args()

    now = datetime.now(KST)
    if now.weekday() == 6:  # 일요일 아침은 스킵 (토요일 아침 = 미 금요장 마감 브리핑)
        print("일요일 — 스킵")
        return

    track_rows: list = []
    pf_text, n_alerts = portfolio_section()
    sc_text = screener_section(track_rows)

    head = f"📈 *TradingAgents 브리핑* — {now.strftime('%m/%d %a')}"
    if n_alerts:
        head += f"\n⚠️ *신호 {n_alerts}건 발생 — 포트폴리오 확인 필요*"
    msg = "\n\n".join(x for x in [head, pf_text, sc_text] if x.strip())

    if args.dry:
        print(msg)
        print("\n[dry] 트랙레코드", len(track_rows), "행 (기록 생략)")
        return

    tracked = save_track(track_rows)
    from agents.notify import send_telegram
    sent = send_telegram(msg)
    print(f"sent={sent} tracked={tracked} alerts={n_alerts}")


if __name__ == "__main__":
    main()

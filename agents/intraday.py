# -*- coding: utf-8 -*-
"""
장중 손절 와처 — 장중 30분~1시간 주기로 전 계좌 포지션의 손절/익절 신호를 점검해
즉시 텔레그램 통보한다. (운용사의 리스크 데스크 역할)

원칙:
  - 알림 전용, 집행 없음 — 자동 청산은 아침 브리핑(전일 종가 기준)이 담당.
    장중가는 네이버/야후 일봉의 당일 미확정 캔들 종가를 사용 (30분 지연 수준이면 충분).
  - 같은 날 같은 (계좌, 종목, 신호)는 1회만 알림 (_장중알림 시트로 중복 차단).
  - 장 시간 밖의 시장은 포지션 단위로 건너뜀 (KR 09:00~15:40 KST, US 09:30~16:10 ET).
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

ALERT_SHEET = "_장중알림"
ALERT_HEADER = ["날짜", "시각", "계좌", "코드", "종목", "신호", "현재가"]

_WATCH_SIGNALS = ("손절", "익절")


def market_open(market: str) -> bool:
    """해당 시장이 장중인지 (마감 직후 10분 여유 포함)."""
    if market == "US":
        now = datetime.now(ZoneInfo("America/New_York"))
        if now.weekday() >= 5:
            return False
        t = now.hour * 60 + now.minute
        return 9 * 60 + 30 <= t <= 16 * 60 + 10
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    if now.weekday() >= 5:
        return False
    t = now.hour * 60 + now.minute
    return 9 * 60 <= t <= 15 * 60 + 40


def _alert_ws():
    import gspread
    from .portfolio_agent import _spreadsheet
    sh = _spreadsheet()
    try:
        ws = sh.worksheet(ALERT_SHEET)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=ALERT_SHEET, rows=2000, cols=8)
        ws.update(values=[ALERT_HEADER], range_name="A1")
    return ws


def run(dry: bool = False) -> tuple[str, int]:
    """전 계좌 장중 점검. (텔레그램 메시지, 신규 경보 수) 반환 — 경보 없으면 ("", 0)."""
    from . import portfolio_agent

    kst_now = datetime.now(ZoneInfo("Asia/Seoul"))
    today = kst_now.strftime("%Y-%m-%d")

    try:
        ws = _alert_ws()
        # 시트가 코드를 숫자로 저장(010120→10120)하므로 _norm_code로 양쪽 정규화
        sent = {(str(r.get("날짜")), str(r.get("계좌")),
                 portfolio_agent._norm_code(str(r.get("코드"))), str(r.get("신호")))
                for r in ws.get_all_records()}
    except Exception as e:
        return f"(장중알림 시트 실패: {e})", 0

    lines: list[str] = []
    rows: list[list] = []
    n_checked = 0
    try:
        for pf in portfolio_agent.list_portfolios():
            positions = [p for p in portfolio_agent.load_positions(pf)
                         if market_open(p.market)]
            for p in positions:
                n_checked += 1
                try:
                    sig = portfolio_agent.calc_signal(p)
                except Exception:
                    continue
                if sig.error or sig.signal not in _WATCH_SIGNALS:
                    continue
                key = (today, pf, sig.code, sig.signal)
                if key in sent:
                    continue
                sent.add(key)
                rows.append([today, kst_now.strftime("%H:%M"), pf, sig.code,
                             sig.name, sig.signal, sig.current_price])
                lines.append(f"🚨 [{pf}] *{sig.signal}*: {sig.name} — {sig.signal_reason}")
    except Exception as e:
        return f"(장중 점검 실패: {e})", 0

    if not lines:
        print(f"장중 점검 {n_checked}포지션 — 신규 경보 없음")
        return "", 0

    if not dry:
        try:
            ws.append_rows(rows)
        except Exception:
            pass

    head = (f"⚡ *장중 신호* — {kst_now.strftime('%m/%d %H:%M')} KST\n"
            f"(자동 집행은 내일 아침 브리핑 — 즉시 대응은 직접 판단)")
    return head + "\n" + "\n".join(lines), len(lines)

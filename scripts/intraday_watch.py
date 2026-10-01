# -*- coding: utf-8 -*-
"""장중 손절 와처 실행 스크립트 (GitHub Actions 장중 크론용).

로컬 테스트: python scripts/intraday_watch.py --dry  (시트 기록·발송 없이 출력만)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="기록·발송 없이 출력만")
    args = ap.parse_args()

    from agents import intraday

    if not (intraday.market_open("KR") or intraday.market_open("US")):
        print("장외 시간 — 스킵")
        return

    msg, n = intraday.run(dry=args.dry)
    if not msg:
        return
    print(msg)
    if args.dry:
        print(f"[dry] 경보 {n}건 (발송 생략)")
        return
    from agents.notify import send_telegram
    print(f"sent={send_telegram(msg)} alerts={n}")


if __name__ == "__main__":
    main()

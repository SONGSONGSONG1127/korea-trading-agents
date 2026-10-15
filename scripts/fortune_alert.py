# -*- coding: utf-8 -*-
"""오늘의 운세 단독 발송 (매일 08:00 KST 크론용).

로컬 테스트: python scripts/fortune_alert.py --dry
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    from agents import fortune
    msg = fortune.telegram_section()
    if not msg:
        print("SAJU_* 미설정 — 스킵")
        return
    print(msg)
    if args.dry:
        return
    from agents.notify import send_telegram
    print("sent =", send_telegram(msg))


if __name__ == "__main__":
    main()

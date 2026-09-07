# -*- coding: utf-8 -*-
"""텔레그램 알림. secrets(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID) 또는 환경변수 사용."""
from __future__ import annotations

import os

import requests


def _cred(key: str) -> str:
    val = os.environ.get(key, "")
    if val:
        return val
    try:
        import streamlit as st
        return str(st.secrets.get(key, ""))
    except Exception:
        return ""


def send_telegram(text: str) -> bool:
    """메시지 발송. 토큰/챗ID 미설정 또는 실패 시 False."""
    token = _cred("TELEGRAM_BOT_TOKEN")
    chat_id = _cred("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown",
                  "disable_web_page_preview": True},
            timeout=15,
        )
        return bool(r.ok and r.json().get("ok"))
    except Exception:
        return False

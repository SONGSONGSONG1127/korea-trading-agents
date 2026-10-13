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


def send_telegram_photo(png: bytes, caption: str = "") -> bool:
    """차트 이미지 발송 (캡션 최대 1024자)."""
    token = _cred("TELEGRAM_BOT_TOKEN")
    chat_id = _cred("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendPhoto",
            data={"chat_id": chat_id, "caption": caption[:1024]},
            files={"photo": ("chart.png", png, "image/png")},
            timeout=30,
        )
        return bool(r.ok and r.json().get("ok"))
    except Exception:
        return False


def send_telegram_document(data: bytes, filename: str, caption: str = "") -> bool:
    """파일(PDF 등) 발송 — 받은 파일을 전달하면 그대로 공유된다."""
    token = _cred("TELEGRAM_BOT_TOKEN")
    chat_id = _cred("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendDocument",
            data={"chat_id": chat_id, "caption": caption[:1024]},
            files={"document": (filename, data, "application/octet-stream")},
            timeout=60,
        )
        return bool(r.ok and r.json().get("ok"))
    except Exception:
        return False

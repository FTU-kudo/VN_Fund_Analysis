# -*- coding: utf-8 -*-
"""Gửi thông báo Telegram (tùy chọn). Cần biến môi trường TELEGRAM_BOT_TOKEN và TELEGRAM_CHAT_ID."""
from __future__ import annotations

import logging
import os

import requests

log = logging.getLogger("vnfa.notify")


def send_telegram(text: str) -> bool:
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        log.info("Không cấu hình Telegram, bỏ qua thông báo.")
        return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat, "text": text[:3900]}, timeout=20)
        r.raise_for_status()
        return True
    except requests.RequestException as e:
        log.warning("Gửi Telegram lỗi: %s", type(e).__name__)  # không log token
        return False

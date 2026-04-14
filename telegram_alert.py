from __future__ import annotations

import os
import requests
from typing import Optional

def send_message(token: str, chat_id: str, text: str, parse_mode: str = "HTML") -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    r = requests.post(url, json=payload, timeout=20)
    r.raise_for_status()

def get_token_from_env() -> Optional[str]:
    return os.environ.get("TELEGRAM_BOT_TOKEN")

def get_chat_id_from_env() -> Optional[str]:
    return os.environ.get("TELEGRAM_CHAT_ID")

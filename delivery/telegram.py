"""
Telegram delivery — sends the digest via Telegram Bot API.

Setup:
  1. Create a bot via @BotFather -> get TELEGRAM_BOT_TOKEN
  2. Send the bot any message, then visit:
       https://api.telegram.org/bot<TOKEN>/getUpdates
     to find your TELEGRAM_CHAT_ID.

Set both in daily-digest/.env.
"""

import os
import requests


def send_telegram(messages: list[str]) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    if not token or not chat_id:
        print("  [telegram] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set — skipping.")
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    for i, text in enumerate(messages):
        try:
            resp = requests.post(url, json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }, timeout=15)
            resp.raise_for_status()
            print(f"  [telegram] Message {i + 1}/{len(messages)} sent.")
        except requests.HTTPError as e:
            print(f"  [telegram] ERROR {e.response.status_code}: {e.response.text}")
            raise
        except Exception as e:
            print(f"  [telegram] ERROR: {e}")
            raise

"""텔레그램 Bot API 전송. 토큰이 없으면 콘솔 출력으로 대체한다."""

from __future__ import annotations

import os
import time

import requests

API = "https://api.telegram.org"


class Telegram:
    def __init__(self, chat_id: str, token: str | None = None):
        self.chat_id = chat_id
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "")

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str) -> None:
        if not self.enabled:
            print("\n[텔레그램 미설정 — 콘솔 출력]\n" + "-" * 40)
            print(text)
            print("-" * 40)
            return

        res = requests.post(
            f"{API}/bot{self.token}/sendMessage",
            json={
                "chat_id": self.chat_id,
                "text": text,
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        if res.status_code != 200:
            raise RuntimeError(f"텔레그램 전송 실패 HTTP {res.status_code}: {res.text[:200]}")

    def send_all(self, chunks: list[str]) -> None:
        for i, part in enumerate(chunks):
            if i > 0:
                time.sleep(0.5)  # Bot API 초당 제한 회피
            self.send(part)

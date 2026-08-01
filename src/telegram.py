"""텔레그램 Bot API 전송.

토큰/chat_id 는 환경변수로 받는다 (저장소가 public 이라 파일에 적지 않는다).
  TELEGRAM_BOT_TOKEN
  TELEGRAM_CHAT_ID

⚠ 설정이 없으면 콘솔 출력으로 대체하되 **조용히 넘어가지 않는다**. 실제로
GitHub Actions 에서 secret 이 전달되지 않아 리포트가 콘솔에만 찍힌 적이 있는데,
로그를 열어보기 전에는 알 수 없었다. 그래서 미설정은 경고로 크게 남기고
`missing_reason` 으로 무엇이 비었는지 짚어 준다.
"""

from __future__ import annotations

import os
import sys
import time

import requests

API = "https://api.telegram.org"

# Bot API 자체의 rate limit(429). 네이버 429 와 달리 retry_after 를 알려주므로
# 그대로 기다렸다 다시 보내면 된다.
SEND_RETRIES = 3


class TelegramError(RuntimeError):
    pass


class Telegram:
    def __init__(self, chat_id: str = "", token: str | None = None):
        # 환경변수 우선, config.yaml 값은 로컬 폴백 (config.py 와 같은 규칙)
        self.chat_id = str(os.environ.get("TELEGRAM_CHAT_ID") or chat_id or "").strip()
        self.token = (token if token is not None
                      else os.environ.get("TELEGRAM_BOT_TOKEN", "")).strip()

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def missing_reason(self) -> str:
        """무엇이 비어서 못 보내는지. 설정돼 있으면 빈 문자열."""
        missing = []
        if not self.token:
            missing.append("TELEGRAM_BOT_TOKEN")
        if not self.chat_id:
            missing.append("TELEGRAM_CHAT_ID")
        return " · ".join(missing)

    def warn_if_disabled(self) -> None:
        if self.enabled:
            return
        print(
            f"[경고] 텔레그램 미설정 — {self.missing_reason()} 이(가) 비어 있습니다. "
            f"리포트를 콘솔로만 출력합니다.\n"
            f"        GitHub Actions: Settings → Secrets and variables → Actions 에 "
            f"같은 이름으로 등록했는지 확인하세요.",
            file=sys.stderr, flush=True,
        )

    def send(self, text: str) -> None:
        if not self.enabled:
            self.warn_if_disabled()
            print("\n[텔레그램 미설정 — 콘솔 출력]\n" + "-" * 40)
            print(text)
            print("-" * 40)
            return

        for attempt in range(SEND_RETRIES):
            res = requests.post(
                f"{API}/bot{self.token}/sendMessage",
                json={
                    "chat_id": self.chat_id,
                    "text": text,
                    "disable_web_page_preview": True,
                },
                timeout=15,
            )
            if res.status_code == 200:
                return

            detail = self._describe(res)
            # 429 는 retry_after 만큼 기다리면 풀린다. 5xx 는 잠깐 뒤 재시도.
            if res.status_code in (429, 500, 502, 503, 504) and attempt < SEND_RETRIES - 1:
                wait = self._retry_after(res)
                print(f"[텔레그램] {detail} — {wait:.0f}초 후 재전송",
                      file=sys.stderr, flush=True)
                time.sleep(wait)
                continue
            raise TelegramError(f"텔레그램 전송 실패 — {detail}")

    def send_all(self, chunks: list[str]) -> None:
        for i, part in enumerate(chunks):
            if i > 0:
                time.sleep(0.5)  # Bot API 초당 제한 회피
            self.send(part)

    # --- 진단 --------------------------------------------------------------

    def _describe(self, res: requests.Response) -> str:
        """Bot API 의 실패 사유를 사람이 읽을 수 있게. 토큰은 절대 싣지 않는다."""
        try:
            body = res.json()
            desc = str(body.get("description") or "")
        except ValueError:
            desc = res.text[:200]

        hint = {
            401: " (토큰이 틀렸습니다 — BotFather 에서 다시 확인하세요)",
            400: " (chat_id 가 틀렸거나 봇이 그 대화에 없습니다. 봇에게 먼저 "
                 "메시지를 한 번 보내세요)",
            403: " (봇이 차단됐거나 대화를 시작하지 않았습니다)",
        }.get(res.status_code, "")
        return f"HTTP {res.status_code}: {desc}{hint}"

    @staticmethod
    def _retry_after(res: requests.Response) -> float:
        try:
            return float((res.json().get("parameters") or {}).get("retry_after") or 3)
        except ValueError:
            return 3.0

    def test(self) -> bool:
        """설정이 실제로 동작하는지 확인한다 (--test-telegram)."""
        if not self.enabled:
            self.warn_if_disabled()
            return False

        # getMe 로 토큰을, sendMessage 로 chat_id 를 각각 확인한다.
        res = requests.get(f"{API}/bot{self.token}/getMe", timeout=15)
        if res.status_code != 200:
            print(f"[실패] 토큰 확인 — {self._describe(res)}", file=sys.stderr)
            return False
        name = (res.json().get("result") or {}).get("username", "?")
        print(f"[확인] 봇 @{name} · chat_id {self.chat_id}", file=sys.stderr)

        self.send("✅ 부동산 모니터 연결 테스트 — 이 메시지가 보이면 설정 완료입니다.")
        print("[확인] 테스트 메시지 전송 완료", file=sys.stderr)
        return True

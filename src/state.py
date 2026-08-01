"""실행 상태(마지막 성공일 / 마지막 실패 알림일)를 담는 작은 JSON.

네이버 429 는 IP 단위로 40분 넘게 지속된다(DESIGN.md 1.5 실측). 같은 job 안에서
재시도해 봐야 IP 가 그대로라 잘 안 풀린다. 그래서 하루에 여러 번 실행해
**매번 다른 러너 IP** 로 시도하고, 한 번 성공하면 그날 나머지 실행은 건너뛴다.
그 "오늘 이미 됐는가"를 여기에 기록한다.

data/ 아래에 두므로 워크플로의 자동 커밋에 같이 실려 실행 간에 유지된다.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))


def today() -> str:
    """KST 기준 날짜. 러너는 UTC 라 그냥 date.today() 를 쓰면 하루가 밀린다."""
    return datetime.now(KST).strftime("%Y-%m-%d")


class State:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._data: dict[str, str] = {}
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                self._data = {}

    def done_today(self) -> bool:
        return self._data.get("last_success") == today()

    def mark_success(self) -> None:
        self._data["last_success"] = today()
        self._save()

    def should_notify_failure(self) -> bool:
        """실패 알림은 하루 한 번만. 하루 3번 시도하면 3번 울릴 이유가 없다."""
        return self._data.get("last_failure_notice") != today()

    def mark_failure_notified(self) -> None:
        self._data["last_failure_notice"] = today()
        self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=1, sort_keys=True),
            encoding="utf-8",
        )

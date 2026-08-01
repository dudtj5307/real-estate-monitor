"""실행 상태(마지막 성공일 / 마지막 실패 알림일 / 단지별 성공일)를 담는 작은 JSON.

네이버 429 는 IP 단위로 40분 넘게 지속된다(DESIGN.md 1.5 실측). 같은 job 안에서
재시도해 봐야 IP 가 그대로라 잘 안 풀린다. 그래서 하루에 여러 번 실행해
**매번 다른 러너 IP** 로 시도하고, 한 번 성공하면 그날 나머지 실행은 건너뛴다.
그 "오늘 이미 됐는가"를 여기에 기록한다.

단지별로도 기록한다. 아침에 A 만 되고 B 가 막혔다면 점심 실행은 **B 만** 부른다.
요청 수가 줄면 새 IP 가 차단에 걸릴 확률도 그만큼 준다.

data/ 아래에 두므로 워크플로의 자동 커밋에 같이 실려 실행 간에 유지된다.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

KST = timezone(timedelta(hours=9))


def today() -> str:
    """KST 기준 날짜. 러너는 UTC 라 그냥 date.today() 를 쓰면 하루가 밀린다."""
    return datetime.now(KST).strftime("%Y-%m-%d")


class State:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._data: dict[str, Any] = {}
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self._data = loaded
            except json.JSONDecodeError:
                self._data = {}

    def done_today(self) -> bool:
        return self._data.get("last_success") == today()

    def mark_success(self) -> None:
        self._data["last_success"] = today()
        self._save()

    # --- 단지별 -------------------------------------------------------------

    def _complexes(self) -> dict[str, str]:
        got = self._data.get("complexes")
        return got if isinstance(got, dict) else {}

    def complex_done_today(self, number: str) -> bool:
        return self._complexes().get(str(number)) == today()

    def mark_complex_done(self, number: str) -> None:
        done = dict(self._complexes())
        done[str(number)] = today()
        # 오래된 날짜는 버린다. 이 파일은 매 실행 커밋되므로 작게 유지한다.
        self._data["complexes"] = {k: v for k, v in done.items() if v == today()}
        self._save()

    # --- 실패 알림 ----------------------------------------------------------

    def should_notify_failure(self) -> bool:
        """실패 알림은 하루 한 번만. 하루 여러 번 시도하면서 매번 울릴 이유가 없다."""
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

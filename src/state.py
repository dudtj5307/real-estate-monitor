"""실행 상태(마지막 성공일 / 마지막 실패 알림일 / 단지별 성공일)를 담는 작은 JSON.

수집 요청은 하루에 여러 번 온다 — 예약 08:30 · 12:30, 대시보드 버튼, 그리고
Pi 가 죽은 날의 watchdog 폴백. 한 번 성공했으면 나머지는 건너뛰어야 중복 수집과
중복 알림이 없다. 그 "오늘 이미 됐는가"를 여기에 기록한다.

덜 부르는 것이 곧 429 예방이기도 하다. 네이버 429 는 IP 단위로 40분 넘게
지속되는데(DESIGN.md 1.5 실측) **집 IP 는 바뀌지 않으므로** 한 번 찍히면
기다리는 것 외에 회복 수단이 없다 (DESIGN-PI.md §7.1).

단지별로도 기록한다. 아침에 A 만 되고 B 가 막혔다면 12:30 요청은 **B 만** 부른다.

data/ 아래에 두므로 커밋에 같이 실려 실행 간에 유지된다. 평상시 커밋 주체는
Pi 하나이고, watchdog 폴백이 도는 날만 예외다.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

KST = timezone(timedelta(hours=9))


def today() -> str:
    """KST 기준 날짜. Pi 와 러너가 같은 파일을 읽고 쓰는데 러너는 UTC 라,
    date.today() 를 쓰면 자정 근처에서 서로 다른 날을 보게 된다."""
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

"""보낼 메시지를 저장소에 남긴다 — 실제 전송 주체는 GitHub Actions.

라즈베리파이에는 비밀값을 두지 않는다. 24시간 켜져 있는 집 기기에 토큰을 두는 대신
Pi 는 완성된 메시지를 여기에 적어 push 하고, push 를 감지한 `notify.yml` 이 저장소
secret 으로 전송한다 (DESIGN-PI.md §5.4).

⚠️ `generated_at` 을 반드시 싣는다. 내용이 어제와 똑같으면 git 이 변경을 보지 못해
커밋도 push 도 없고, push 가 없으면 워크플로가 돌지 않는다. 그러면 '변동 없음'
리포트만 조용히 사라져 아무도 눈치채지 못한다. 시각이 매번 달라 그 일이 없어진다.

전송 쪽(bash + jq)이 길이 제한을 다시 계산하지 않도록 여기서 미리 쪼개 둔다.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from . import report
from .state import KST

# 스키마가 바뀌면 올린다. notify.yml 이 모르는 버전을 만나면 보내지 않고 실패한다 —
# 조용히 잘못된 메시지를 보내는 것보다 알림이 끊기고 워크플로가 빨개지는 게 낫다.
VERSION = 1


def write(path: str | Path, kind: str, text: str) -> Path:
    """보낼 메시지를 `path` 에 기록한다.

    kind 는 `report`(정상 리포트) 또는 `failure`(수집 실패 알림).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "version": VERSION,
        "kind": kind,
        "generated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
        "chunks": report.chunk(text),
    }
    # ensure_ascii=False — 저장소에서 사람이 그대로 읽을 수 있어야 진단이 쉽다.
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path

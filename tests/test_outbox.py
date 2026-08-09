"""--outbox 전달 경로 검증 (Pi 가 적고 GitHub Actions 가 보낸다).

네트워크·텔레그램을 타지 않는다 — NaverClient 와 Telegram 을 가짜로 바꾸고,
상태·스냅샷·outbox 경로를 임시 폴더로 돌린다.

    python tests/test_outbox.py
"""

import json
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import main as m
from src import outbox
from src.naver import Article, IPBlocked
from src.state import KST, State

SENT: list[str] = []


class FakeTelegram:
    """호출되면 안 된다 — --outbox 는 여기를 타지 않는다."""

    def __init__(self, chat_id=""):
        pass

    enabled = True

    def warn_if_disabled(self):
        SENT.append("<경고>")

    def send(self, text):
        SENT.append(text)

    def send_all(self, chunks):
        SENT.extend(chunks)


class OkClient:
    def __init__(self, *a, **kw):
        pass

    def fetch(self, number, _trades):
        return [Article(f"a{number}", "테스트", "매매", "101", "5/20",
                        84.0, 105.0, 95000, 0, "남향", "", "", "26.08.01.", 1)]


class BlockedClient:
    def __init__(self, *a, **kw):
        pass

    def fetch(self, _number, _trades):
        raise IPBlocked("첫 요청부터 차단됨(RateLimited)")


def setup(client=OkClient):
    tmp = Path(tempfile.mkdtemp())
    m.STATE_PATH = tmp / "state.json"
    m.SNAPSHOT_PATH = tmp / "snapshot.json"
    m.HTML_PATH = tmp / "index.html"
    m.OUTBOX_PATH = tmp / "outbox.json"
    m.Telegram = FakeTelegram
    m.NaverClient = client
    m.time.sleep = lambda _s: None
    SENT.clear()
    return tmp


def load_box():
    return json.loads(m.OUTBOX_PATH.read_text(encoding="utf-8"))


def test_outbox_replaces_sending():
    setup()

    assert m.main(["--outbox"]) == m.EXIT_OK
    assert SENT == [], f"--outbox 인데 텔레그램을 탔다: {SENT}"
    assert m.OUTBOX_PATH.exists(), "outbox.json 이 만들어지지 않았다"

    box = load_box()
    assert box["version"] == outbox.VERSION
    assert box["kind"] == "report"
    assert box["chunks"], box
    assert "성복역현대홈타운" in "\n".join(box["chunks"])
    print(f"  전송 0회 · outbox {len(box['chunks'])}조각 기록")


def test_no_token_warning_when_outbox():
    """Pi 에는 토큰이 없는 게 정상이다. 미설정 경고가 뜨면 로그가 시끄러워진다."""
    setup()
    m.main(["--outbox"])
    assert "<경고>" not in SENT, "토큰 미설정 경고가 떴다"
    print("  토큰 미설정 경고 없음")


def test_dry_run_beats_outbox():
    """수동 점검(--dry-run)은 파일도 남기지 않아야 한다 — 뜻하지 않은 전송 방지."""
    setup()
    assert m.main(["--outbox", "--dry-run"]) == m.EXIT_OK
    assert not m.OUTBOX_PATH.exists(), "--dry-run 인데 outbox 를 썼다"
    print("  --dry-run > --outbox 우선순위 유지")


def test_failure_notice_goes_through_outbox():
    setup(BlockedClient)

    assert m.main(["--outbox"]) == m.EXIT_BLOCKED
    assert SENT == [], SENT
    box = load_box()
    assert box["kind"] == "failure", box
    assert "수집에 모두 실패" in "\n".join(box["chunks"])
    print("  차단 알림도 outbox 로 (rc=2)")

    # 하루 한 번 규칙은 그대로다 — 파일이 다시 쓰이면 알림이 두 번 간다
    before = m.OUTBOX_PATH.read_text(encoding="utf-8")
    assert m.main(["--outbox"]) == m.EXIT_BLOCKED
    assert m.OUTBOX_PATH.read_text(encoding="utf-8") == before, "실패 알림이 두 번 기록됐다"
    assert not State(m.STATE_PATH).should_notify_failure()
    print("  같은 날 재시도 → outbox 변화 없음")


def test_content_changes_even_when_report_is_identical():
    """'변동 없음' 리포트가 사라지는 함정.

    내용이 어제와 같으면 git 이 변경을 못 봐 push 가 없고, push 가 없으면
    notify.yml 이 돌지 않는다. generated_at 이 그걸 막는다.
    """
    tmp = Path(tempfile.mkdtemp())
    path = tmp / "outbox.json"
    real = outbox.datetime

    class FrozenClock:
        moment = datetime(2026, 8, 9, 8, 40, 0, tzinfo=KST)

        @classmethod
        def now(cls, tz=None):
            return cls.moment

    outbox.datetime = FrozenClock
    try:
        outbox.write(path, "report", "🏠 변동 없음")
        first = path.read_text(encoding="utf-8")

        FrozenClock.moment += timedelta(days=1)
        outbox.write(path, "report", "🏠 변동 없음")
        second = path.read_text(encoding="utf-8")
    finally:
        outbox.datetime = real

    assert first != second, "같은 리포트가 같은 바이트를 남겼다 — push 가 안 생긴다"
    assert "2026-08-09" in first and "2026-08-10" in second
    print("  같은 본문이라도 generated_at 이 달라 커밋이 생긴다")


def test_chunks_reassemble():
    """긴 리포트를 쪼갠 뒤 이어 붙이면 원문이어야 한다 (notify.yml 이 순서대로 보낸다)."""
    tmp = Path(tempfile.mkdtemp())
    path = tmp / "outbox.json"
    text = "\n".join(f"{i}번째 줄 · 매물 정보 {'가' * 60}" for i in range(300))

    outbox.write(path, "report", text)
    chunks = json.loads(path.read_text(encoding="utf-8"))["chunks"]

    assert len(chunks) > 1, "테스트가 분할을 못 건드렸다"
    assert all(len(c) <= 4096 for c in chunks), [len(c) for c in chunks]
    assert "\n".join(chunks) == text
    print(f"  {len(chunks)}조각으로 나뉘고 원문 복원됨")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            print(f"[{name}]")
            fn()
    print("\n전부 통과")

"""--skip-if-done / 실패 알림 1일 1회 로직 검증.

네트워크·텔레그램을 타지 않는다 — NaverClient 와 Telegram 을 가짜로 바꾸고,
상태·스냅샷 경로를 임시 폴더로 돌린다.

    python tests/test_state.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import main as m
from src.naver import RateLimited
from src.state import State, today

SENT: list[str] = []


class FakeTelegram:
    def __init__(self, chat_id):
        pass

    def send(self, text):
        SENT.append(text)

    def send_all(self, chunks):
        SENT.extend(chunks)


class BlockedClient:
    """언제나 429 를 내는 클라이언트."""

    def __init__(self, *a, **kw):
        pass

    def fetch(self, _number, _trades):
        raise RateLimited("429 Too Many Requests — 재시도 2회 후에도 차단")


def setup():
    tmp = Path(tempfile.mkdtemp())
    m.STATE_PATH = tmp / "state.json"
    m.SNAPSHOT_PATH = tmp / "snapshot.json"
    m.HTML_PATH = tmp / "index.html"
    m.Telegram = FakeTelegram
    m.NaverClient = BlockedClient
    m.time.sleep = lambda _s: None  # 단지 간 25초 대기 건너뛰기
    SENT.clear()


def test_all_blocked_notifies_once():
    setup()

    assert m.main([]) == 1
    assert len(SENT) == 1, SENT
    assert "다른 IP 로 다시 시도" in SENT[0], SENT[0]
    print("  전건 429 → 알림 1건")

    # 같은 날 두 번째 시도는 조용해야 한다 (하루 3번 실행하므로)
    assert m.main([]) == 1
    assert len(SENT) == 1, SENT
    print("  같은 날 재시도 → 알림 억제")


def test_skip_if_done():
    setup()

    # 아직 성공한 적이 없으면 건너뛰지 않는다
    assert m.main(["--skip-if-done"]) == 1
    print("  미성공 상태 → 건너뛰지 않음")

    State(m.STATE_PATH).mark_success()
    before = len(SENT)
    assert m.main(["--skip-if-done"]) == 0
    assert len(SENT) == before, SENT
    print("  성공 기록 후 → 즉시 종료(rc=0), 알림 없음")

    # 수동 실행("지금 갱신")은 성공했어도 다시 수집한다
    assert m.main([]) == 1
    print("  수동 실행 → 건너뛰지 않음")


def test_state_uses_kst_date():
    tmp = Path(tempfile.mkdtemp())
    st = State(tmp / "state.json")

    assert not st.done_today()
    st.mark_success()
    assert st.done_today()

    assert st.should_notify_failure()
    st.mark_failure_notified()
    assert not st.should_notify_failure()

    # 새로 읽어도 유지돼야 한다 (워크플로가 커밋해 다음 실행이 읽는다)
    assert State(tmp / "state.json").done_today()
    print(f"  today()={today()} · 기록/판정/재로드 일치")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            print(f"[{name}]")
            fn()
    print("\n전부 통과")

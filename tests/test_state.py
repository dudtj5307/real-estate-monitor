"""--skip-if-done / 단지별 재개 / 실패 알림 1일 1회 로직 검증.

네트워크·텔레그램을 타지 않는다 — NaverClient 와 Telegram 을 가짜로 바꾸고,
상태·스냅샷 경로를 임시 폴더로 돌린다.

    python tests/test_state.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import main as m
from src.naver import Article, IPBlocked
from src.state import State, today

SENT: list[str] = []


class FakeTelegram:
    def __init__(self, chat_id=""):
        pass

    enabled = True

    def warn_if_disabled(self):
        pass

    def send(self, text):
        SENT.append(text)

    def send_all(self, chunks):
        SENT.extend(chunks)


class BlockedClient:
    """첫 요청부터 막힌 클라이언트 (= 러너 IP 차단)."""

    def __init__(self, *a, **kw):
        pass

    def fetch(self, _number, _trades):
        raise IPBlocked("첫 요청부터 차단됨(RateLimited)")


class OkClient:
    """언제나 매물 1건을 돌려주는 클라이언트."""

    fetched: list[str] = []

    def __init__(self, *a, **kw):
        pass

    def fetch(self, number, _trades):
        OkClient.fetched.append(number)
        return [Article(f"a{number}", "테스트", "매매", "101", "5/20",
                        84.0, 105.0, 95000, 0, "남향", "", "", "26.08.01.", 1)]


def setup(client=BlockedClient):
    tmp = Path(tempfile.mkdtemp())
    m.STATE_PATH = tmp / "state.json"
    m.SNAPSHOT_PATH = tmp / "snapshot.json"
    m.HTML_PATH = tmp / "index.html"
    m.Telegram = FakeTelegram
    m.NaverClient = client
    m.time.sleep = lambda _s: None  # 단지 간 25초 대기 건너뛰기
    SENT.clear()
    OkClient.fetched.clear()


def test_all_blocked_notifies_once():
    setup()

    # 차단은 예상된 실패 → 종료 코드 2 (워크플로가 빨간 X 로 만들지 않는다)
    assert m.main([]) == m.EXIT_BLOCKED
    assert len(SENT) == 1, SENT
    assert "다른 IP 로 다시 시도" in SENT[0], SENT[0]
    print("  전건 차단 → 알림 1건 · rc=2")

    # 같은 날 두 번째 시도는 조용해야 한다 (하루 여러 번 실행하므로)
    assert m.main([]) == m.EXIT_BLOCKED
    assert len(SENT) == 1, SENT
    print("  같은 날 재시도 → 알림 억제")


def test_ip_blocked_skips_remaining_complexes():
    """첫 단지가 IP 차단이면 나머지 단지는 부르지도 않아야 한다."""
    setup()
    tried = {"n": 0}

    class CountingClient(BlockedClient):
        def fetch(self, number, trades):
            tried["n"] += 1
            return super().fetch(number, trades)

    m.NaverClient = CountingClient
    m.main([])

    assert tried["n"] == 1, tried
    print(f"  단지 여러 개라도 호출 {tried['n']}회에서 중단")


def test_skip_if_done():
    setup()

    # 아직 성공한 적이 없으면 건너뛰지 않는다
    assert m.main(["--skip-if-done"]) == m.EXIT_BLOCKED
    print("  미성공 상태 → 건너뛰지 않음")

    State(m.STATE_PATH).mark_success()
    before = len(SENT)
    assert m.main(["--skip-if-done"]) == m.EXIT_OK
    assert len(SENT) == before, SENT
    print("  성공 기록 후 → 즉시 종료(rc=0), 알림 없음")

    # 수동 실행("지금 갱신")은 성공했어도 다시 수집한다
    assert m.main([]) == m.EXIT_BLOCKED
    print("  수동 실행 → 건너뛰지 않음")


def test_resume_only_missing_complexes():
    """오늘 이미 수집한 단지는 다시 부르지 않는다 (요청 수 = 차단 위험)."""
    setup(OkClient)

    assert m.main(["--skip-if-done"]) == m.EXIT_OK
    first = list(OkClient.fetched)
    assert len(first) >= 2, first
    print(f"  1회차: {len(first)}개 단지 수집")

    # 하루가 끝난 게 아니라 '단지별로' 끝난 상태를 만든다
    st = State(m.STATE_PATH)
    st._data.pop("last_success", None)
    st._save()

    OkClient.fetched.clear()
    assert m.main(["--skip-if-done"]) == m.EXIT_OK
    assert OkClient.fetched == [], OkClient.fetched
    print("  2회차: 이미 끝난 단지는 호출 0회")

    # 대시보드에서 단지가 사라지면 안 된다 (스냅샷에서 되살린다)
    html = m.HTML_PATH.read_text(encoding="utf-8")
    assert "성복역현대홈타운" in html, "건너뛴 단지가 대시보드에서 사라졌다"
    print("  건너뛴 단지도 대시보드에 남음")


def test_state_uses_kst_date():
    tmp = Path(tempfile.mkdtemp())
    st = State(tmp / "state.json")

    assert not st.done_today()
    st.mark_success()
    assert st.done_today()

    assert not st.complex_done_today("8692")
    st.mark_complex_done("8692")
    assert st.complex_done_today("8692")
    assert not st.complex_done_today("3707")

    assert st.should_notify_failure()
    st.mark_failure_notified()
    assert not st.should_notify_failure()

    # 새로 읽어도 유지돼야 한다 (워크플로가 커밋해 다음 실행이 읽는다)
    reloaded = State(tmp / "state.json")
    assert reloaded.done_today()
    assert reloaded.complex_done_today("8692")
    print(f"  today()={today()} · 기록/판정/재로드 일치")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            print(f"[{name}]")
            fn()
    print("\n전부 통과")

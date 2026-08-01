"""429/타임아웃 재시도 정책 검증.

핵심 규칙 (DESIGN.md 1.5):
  · 이 실행에서 아직 한 건도 못 받아왔다면 **재시도하지 않는다**.
    러너 IP 가 통째로 막힌 상태라 기다려도 같은 IP 로는 안 풀린다 → IPBlocked
  · 앞 단지가 성공한 뒤의 429 는 우리 호출 속도 탓일 수 있으므로 짧게 재시도한다.

네트워크를 타지 않는다 — `_fetch_once` / `_warmup` 을 가짜로 바꿔 돌린다.

    python tests/test_retry.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

from src.naver import Article, IPBlocked, NaverClient, RateLimited


def article() -> Article:
    return Article("1", "테스트", "매매", "101", "5/20", 84.0, 105.0,
                   95000, 0, "남향", "", "", "26.08.01.", 1)


def client(**kw) -> NaverClient:
    # 실제 간격(45초/2분)으로 돌리면 테스트가 몇 분 걸린다
    return NaverClient(warmup_delay=0, page_delay=0,
                       retry_waits=(0.01, 0.02, 0.03), **kw)


def warm(c: NaverClient) -> None:
    """'매물 API 가 이미 한 번 정상 응답한 상태'로 만든다 — 재시도가 켜지는 조건.

    실제로는 _post_article_list 가 200 을 받을 때 올라간다.
    """
    c.ok_count = 1


def run_case(name: str, fail_times: int, *, budget: float = 60.0,
             warmed: bool = True) -> int:
    """fail_times 회 실패한 뒤 성공하는 클라이언트를 돌리고 호출 횟수를 돌려준다."""
    c = client(retry_budget=budget)
    if warmed:
        warm(c)
    calls = {"n": 0}

    def fake(_number, _trades):
        calls["n"] += 1
        if calls["n"] <= fail_times:
            raise RateLimited("429")
        return [article()]

    c._fetch_once = fake
    try:
        c.fetch("8692", ["매매"])
        print(f"  {name}: 성공 (호출 {calls['n']}회)")
    except RateLimited as exc:
        print(f"  {name}: 실패 (호출 {calls['n']}회, {type(exc).__name__})")
    return calls["n"]


def test_first_failure_of_run_is_not_retried():
    """첫 요청부터 429 = 이미 오염된 IP. 두드리지 않고 즉시 포기한다."""
    c = client()
    calls = {"n": 0}

    def always_fail(_number, _trades):
        calls["n"] += 1
        raise RateLimited("429")

    c._fetch_once = always_fail
    try:
        c.fetch("8692", ["매매"])
        raise AssertionError("차단인데 성공했다")
    except IPBlocked as exc:
        assert "첫 요청부터" in str(exc), exc
    assert calls["n"] == 1, calls
    assert c.retry_left == 4 * 60.0, "재시도 예산을 쓰지 않아야 한다"
    print(f"  첫 요청 429 → 호출 {calls['n']}회로 즉시 IPBlocked")


def test_timeout_on_first_request_is_also_ip_blocked():
    """차단은 429 가 아니라 무응답으로도 온다 (DESIGN.md 1.5)."""
    c = client()

    def fake_timeout(_number, _trades):
        raise requests.exceptions.ReadTimeout("read timed out")

    c._fetch_once = fake_timeout
    try:
        c.fetch("8692", ["매매"])
        raise AssertionError("타임아웃인데 성공했다")
    except IPBlocked:
        print("  첫 요청 타임아웃 → IPBlocked")


def test_warmup_429_is_ip_blocked():
    """워밍업 GET 의 429 도 같은 판정을 받아야 한다.

    _new_session() 이 session 을 갈아끼우므로 session.get 이 아니라
    _warmup 자체를 가짜로 둬야 실제 호출이 새지 않는다.
    """
    c = client()
    seen = {"n": 0}

    def fake_warmup(_number):
        seen["n"] += 1
        raise RateLimited("429 Too Many Requests (워밍업)")

    c._warmup = fake_warmup
    try:
        c.fetch("8692", ["매매"])
        raise AssertionError("차단인데 성공했다")
    except IPBlocked:
        pass
    assert seen["n"] == 1, seen
    print(f"  워밍업 429 → 워밍업 {seen['n']}회로 중단")


def test_retries_after_a_success():
    """한 단지가 된 뒤의 429 는 우리 페이스 문제일 수 있어 재시도한다."""
    assert run_case("첫 시도 성공", 0) == 1
    assert run_case("1회 실패 후 성공", 1) == 2
    assert run_case("3회 실패 후 성공", 3) == 4


def test_gives_up_after_retries():
    # retry_waits 가 3개이므로 최초 1회 + 재시도 3회 = 4회에서 포기
    assert run_case("계속 실패", 99) == 4


def test_budget_stops_retrying():
    # 예산 0.015초면 0.01짜리 1회만 쓰고 그 다음 0.02는 못 쓴다
    assert run_case("예산 부족", 99, budget=0.015) == 2


def test_budget_is_consumed():
    c = client(retry_budget=1.0)
    warm(c)

    def always_fail(*_a):
        raise RateLimited("429")

    c._fetch_once = always_fail
    try:
        c.fetch("8692", ["매매"])
    except RateLimited:
        pass
    assert c.retry_left < 1.0, c.retry_left
    print(f"  예산 소비: 1.000초 → {c.retry_left:.3f}초 남음")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            print(f"[{name}]")
            fn()
    print("\n전부 통과")

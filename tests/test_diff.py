"""diff 판정 검증 — 번호가 바뀐 재등록 · 전일 기준 유지.

    python tests/test_diff.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import diff as d
from src.diff import Snapshot, compare
from src.naver import Article


def art(number, price, *, dong="201", floor="10/20", sqm=84.85,
        trade="매매", rent=0):
    return Article(number, "테스트", trade, dong, floor, sqm, 105.0,
                   price, rent, "남향", "", "", "26.08.01.", 1)


def test_same_number_price_change():
    result = compare([art("a", 105000)], [art("a", 100000)])
    assert not result.new and not result.gone, result
    assert len(result.changed) == 1
    assert result.changed[0].old_price == 100000
    assert not result.changed[0].renumbered
    print("  번호 동일 + 가격 변동 → 가격변동 1건")


def test_renumbered_is_price_change_not_new():
    """대표 매물이 바뀌어 번호가 달라져도 같은 집이면 가격변동이어야 한다."""
    result = compare([art("new-id", 103000)], [art("old-id", 105000)])

    assert result.new == [], f"신규로 오보됨: {result.new}"
    assert result.gone == [], f"소진으로 오보됨: {result.gone}"
    assert len(result.changed) == 1, result.changed
    change = result.changed[0]
    assert change.old_price == 105000 and change.renumbered
    print("  번호 변경 + 가격 변동 → 가격변동 1건 (신규/소진 아님)")


def test_renumbered_same_price_is_silent():
    result = compare([art("new-id", 105000)], [art("old-id", 105000)])
    assert result.is_empty, result
    print("  번호만 변경 → 아무 변동도 아님")


def test_far_price_is_not_the_same_home():
    """같은 층이라도 가격이 크게 다르면 다른 호수로 본다 (짝짓지 않는다)."""
    result = compare([art("new-id", 60000)], [art("old-id", 120000)])
    assert len(result.new) == 1 and len(result.gone) == 1, result
    assert not result.changed
    print("  가격 격차 큼 → 신규 + 소진 (억지 매칭 안 함)")


def test_blank_location_never_matches():
    """동·층이 비면 서로 다른 집이 뭉치므로 위치 매칭에서 제외한다."""
    result = compare([art("new-id", 103000, dong="", floor="")],
                     [art("old-id", 105000, dong="", floor="")])
    assert len(result.new) == 1 and len(result.gone) == 1, result
    print("  위치 정보 없음 → 위치 매칭 제외")


def test_multiple_units_on_same_floor():
    """같은 위치 키가 여러 건이면 가격이 가까운 것끼리 짝짓는다."""
    previous = [art("p1", 100000), art("p2", 110000)]
    current = [art("c1", 101000), art("c2", 111000)]
    result = compare(current, previous)

    assert not result.new and not result.gone, result
    pairs = {c.article.article_number: c.old_price for c in result.changed}
    assert pairs == {"c1": 100000, "c2": 110000}, pairs
    print("  같은 층 2건 → 가격 근접순으로 올바르게 짝지음")


def test_real_new_and_gone_still_reported():
    result = compare([art("b", 100000, floor="5/20")],
                     [art("a", 100000, floor="10/20")])
    assert [a.article_number for a in result.new] == ["b"]
    assert [a.article_number for a in result.gone] == ["a"]
    print("  진짜 신규/소진은 그대로 보고")


# --- 스냅샷 기준일 -----------------------------------------------------------

def _snap():
    return Snapshot(Path(tempfile.mkdtemp()) / "snapshot.json")


def test_baseline_holds_within_the_same_day():
    """하루에 여러 번 돌려도 기준은 전일 값이어야 한다.

    러너 IP 차단 때문에 하루 7회 실행한다. 매 실행이 기준을 덮으면 두 번째
    실행부터 전일 대비 변동이 통째로 사라진다 — 사용자가 보는 건 그 결과다.
    """
    snap = _snap()

    # 어제: 10.00억 1건을 저장했다고 가정
    snap._data["8692:매매"] = {
        "date": "2026-08-08",
        "articles": [art("a", 100000).to_dict()],
        "baseline_date": "2026-08-07",
        "baseline": [],
    }

    # 오늘 아침 — 기준은 어제 값
    base = snap.baseline("8692", "매매")
    assert base.date == "2026-08-08"
    assert [a.price for a in base.articles] == [100000]
    snap.set("8692", "매매", [art("a", 105000)])

    # 오늘 낮에 다시 실행 — 기준이 아침 값(10.50억)으로 굴러가면 안 된다
    again = snap.baseline("8692", "매매")
    assert again.date == "2026-08-08", f"기준이 굴러갔다: {again.date}"
    assert [a.price for a in again.articles] == [100000], again.articles
    assert len(compare([art("a", 105000)], again.articles).changed) == 1
    print("  같은 날 재실행 → 기준 유지 (전일 대비 변동 보존)")

    # 저장한 내용을 다시 읽어도 같아야 한다 (워크플로가 커밋해 다음 실행이 읽는다)
    snap.save()
    reloaded = Snapshot(snap.path)
    assert reloaded.baseline("8692", "매매").date == "2026-08-08"
    assert [a.price for a in reloaded.latest("8692", "매매")] == [105000]
    print("  재로드 후에도 기준·최신값 유지")


def test_baseline_rolls_over_on_a_new_day():
    snap = _snap()
    snap._data["8692:매매"] = {
        "date": "2026-08-08",
        "articles": [art("a", 100000).to_dict()],
        "baseline_date": "2026-08-07",
        "baseline": [],
    }
    snap.set("8692", "매매", [art("a", 105000)])  # today() 는 08-08 이 아니다

    entry = snap._data["8692:매매"]
    assert entry["baseline_date"] == "2026-08-08", entry["baseline_date"]
    assert [a["price"] for a in entry["baseline"]] == [100000]
    print("  날짜가 바뀌면 → 직전 목록이 새 기준")


def test_first_run_has_no_baseline():
    snap = _snap()
    base = snap.baseline("8692", "매매")
    assert base.is_first and base.articles == []
    assert len(compare([art("a", 100000)], base.articles).new) == 1
    print("  기준 없음 → 전건 신규 + is_first 표시")


def test_reads_legacy_list_format():
    """구 포맷(매물 목록 리스트)도 읽어야 한다 — 있는 스냅샷을 버리지 않는다."""
    snap = _snap()
    snap._data["8692:매매"] = [art("a", 100000).to_dict()]

    base = snap.baseline("8692", "매매")
    assert [a.price for a in base.articles] == [100000], base.articles
    assert base.is_first  # 날짜를 모르므로 기준일은 빈 값
    print("  구 포맷 리스트 → 비교 기준으로 그대로 사용")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            print(f"[{name}]")
            fn()
    print(f"\n전부 통과 (가격 허용 오차 {d.RENUMBER_PRICE_TOLERANCE:.0%})")

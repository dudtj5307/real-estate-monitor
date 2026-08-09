"""전일 스냅샷과 비교해 신규 / 가격변동 / 소진을 판정한다.

## articleNumber 만으로는 왜 부족한가

네이버가 주는 것은 개별 매물이 아니라 **중복 묶음의 대표 매물**이다
(`representativeArticleInfo` · `duplicatedArticleInfo.realtorCount`). 같은 집을
여러 중개사가 올리면 그중 하나가 대표가 되는데, 대표가 바뀌면 articleNumber 도
같이 바뀐다. 특히 호가를 조정할 때 중개사가 기존 매물을 내리고 새로 올리는 일이
흔해서 **가격이 변한 매물일수록 새 번호로 나타난다.**

번호만 보면 그건 "소진 1건 + 신규 1건"이다. 정작 알고 싶던 가격변동이 사라지고
화면이 신규로만 채워진다. 그래서 두 단계로 맞춘다.

  ① articleNumber 가 그대로인 것끼리 먼저 짝짓는다.
  ② 남은 것들을 물리적 위치(거래유형·동·층·전용면적)로 짝짓는다. 짝이 맞으면
     번호만 바뀐 같은 집으로 보고 가격변동으로 판정한다(`renumbered`).

## 비교 기준은 '직전 실행'이 아니라 '전일'이다

하루에 여러 번 실행한다(러너 IP 차단 회피 — DESIGN.md 1.5). 매 실행이 비교
기준을 덮어쓰면 두 번째 실행부터는 '아침 이후 변동'만 남아 전일 대비 변동이
통째로 사라진다. 그래서 `Snapshot` 은 오늘 값과 비교 기준을 따로 들고,
**기준은 날짜가 바뀔 때만 굴린다.**
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .naver import Article
from .state import today

# 위치로 다시 짝지을 때 허용하는 가격 차이(비율). 실제 호가 조정은 대개 몇 %
# 수준이라, 이 폭을 넘으면 같은 집의 재등록이 아니라 같은 층의 다른 호수로 보는
# 편이 안전하다. 짝을 못 지으면 지금까지처럼 신규 + 소진으로 남는다.
RENUMBER_PRICE_TOLERANCE = 0.2


@dataclass
class PriceChange:
    article: Article
    old_price: int
    old_rent: int
    renumbered: bool = False  # 매물번호가 바뀐 재등록으로 추정 (위 ②)


@dataclass
class Diff:
    new: list[Article] = field(default_factory=list)
    changed: list[PriceChange] = field(default_factory=list)
    gone: list[Article] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.new or self.changed or self.gone)


def compare(current: list[Article], previous: list[Article]) -> Diff:
    diff = Diff()

    # ① 번호가 그대로인 것
    prev_by_id = {a.article_number: a for a in previous}
    matched_prev: set[str] = set()
    leftover_cur: list[Article] = []

    for article in current:
        old = prev_by_id.get(article.article_number)
        if old is None:
            leftover_cur.append(article)
            continue
        matched_prev.add(old.article_number)
        if old.price_key() != article.price_key():
            diff.changed.append(PriceChange(article, old.price, old.rent))

    leftover_prev = [a for a in previous if a.article_number not in matched_prev]

    # ② 번호가 바뀐 것
    still_new, still_gone = _match_by_location(leftover_cur, leftover_prev, diff)
    diff.new.extend(still_new)
    diff.gone.extend(still_gone)
    return diff


def _match_by_location(
    current: list[Article], previous: list[Article], diff: Diff
) -> tuple[list[Article], list[Article]]:
    """번호가 다른 것들끼리 위치로 짝짓는다. 짝지어진 쌍은 diff.changed 로 간다.

    같은 위치 키에 여러 건이 있을 수 있으므로(같은 층의 다른 호수) 가격이 가장
    가까운 것부터 짝짓는다. 짝짓는 순서가 결과를 바꾸지 않도록 가격순으로 돈다.
    """
    pools: dict[tuple, list[Article]] = defaultdict(list)
    for a in previous:
        if a.has_location():
            pools[a.location_key()].append(a)

    used_prev: set[str] = set()
    used_cur: set[str] = set()

    for article in sorted(current, key=lambda a: (a.price, a.rent, a.article_number)):
        if not article.has_location():
            continue
        pool = [p for p in pools.get(article.location_key(), [])
                if p.article_number not in used_prev]
        mate = _closest(article, pool)
        if mate is None:
            continue

        used_prev.add(mate.article_number)
        used_cur.add(article.article_number)
        if mate.price_key() != article.price_key():
            diff.changed.append(
                PriceChange(article, mate.price, mate.rent, renumbered=True)
            )
        # 가격까지 같으면 번호만 바뀐 것이므로 아무 변동도 아니다

    return (
        [a for a in current if a.article_number not in used_cur],
        [a for a in previous if a.article_number not in used_prev],
    )


def _closest(article: Article, pool: list[Article]) -> Article | None:
    best: Article | None = None
    best_gap: float | None = None
    for candidate in pool:
        base = max(article.price, candidate.price, 1)
        gap = (abs(article.price - candidate.price)
               + abs(article.rent - candidate.rent)) / base
        if gap > RENUMBER_PRICE_TOLERANCE:
            continue
        if best_gap is None or gap < best_gap:
            best, best_gap = candidate, gap
    return best


@dataclass
class Baseline:
    """비교 기준. date 가 비어 있으면 기준이 없다(= 그 단지의 첫 수집)."""

    articles: list[Article]
    date: str

    @property
    def is_first(self) -> bool:
        return not self.date


class Snapshot:
    """단지+거래유형 단위로 매물 목록과 비교 기준을 보관하는 JSON 파일.

    한 항목의 구조::

        "8692:매매": {
          "date": "2026-08-09",           # articles 를 수집한 날 (KST)
          "articles": [...],              # 그날 마지막으로 본 목록
          "baseline_date": "2026-08-08",  # articles 를 비교한 기준의 날
          "baseline": [...]
        }

    같은 날 두 번째 실행은 `articles` 만 갱신하고 `baseline` 은 건드리지 않는다.
    그래야 하루 중 언제 돌려도 리포트가 같은 '전일 대비'를 말한다.
    """

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

    @staticmethod
    def key(complex_number: str, trade_type: str) -> str:
        return f"{complex_number}:{trade_type}"

    def exists(self) -> bool:
        return bool(self._data)

    def _entry(self, complex_number: str, trade_type: str) -> dict[str, Any]:
        """구 포맷(매물 목록 리스트)도 읽는다. 날짜가 없으므로 기준일은 빈 값."""
        raw = self._data.get(self.key(complex_number, trade_type))
        if isinstance(raw, list):
            return {"date": "", "articles": raw, "baseline_date": "", "baseline": []}
        if isinstance(raw, dict):
            return raw
        return {"date": "", "articles": [], "baseline_date": "", "baseline": []}

    @staticmethod
    def _load(raw: Any) -> list[Article]:
        return [Article.from_dict(d) for d in (raw or [])]

    def baseline(self, complex_number: str, trade_type: str) -> Baseline:
        """이번 비교에 쓸 기준.

        오늘 이미 저장한 적이 있으면 그때 쓴 기준을 그대로 쓴다(전일 대비 유지).
        오늘 첫 실행이면 마지막으로 저장된 목록이 기준이다.
        """
        entry = self._entry(complex_number, trade_type)
        if entry.get("date") == today():
            return Baseline(self._load(entry.get("baseline")),
                            entry.get("baseline_date") or "")
        return Baseline(self._load(entry.get("articles")), entry.get("date") or "")

    def latest(self, complex_number: str, trade_type: str) -> list[Article]:
        """마지막으로 저장된 목록. 오늘 수집을 건너뛴 단지를 되살릴 때 쓴다."""
        return self._load(self._entry(complex_number, trade_type).get("articles"))

    def set(self, complex_number: str, trade_type: str, articles: list[Article]) -> None:
        entry = self._entry(complex_number, trade_type)
        if entry.get("date") == today():
            # 오늘 두 번째 이후 실행 — 기준은 그대로 두고 오늘 값만 갱신한다
            base_date = entry.get("baseline_date") or ""
            base = entry.get("baseline") or []
        else:
            # 날이 바뀌었다 — 직전에 저장한 목록이 새 기준이 된다
            base_date = entry.get("date") or ""
            base = entry.get("articles") or []

        self._data[self.key(complex_number, trade_type)] = {
            "date": today(),
            "articles": [a.to_dict() for a in articles],
            "baseline_date": base_date,
            "baseline": base,
        }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=1, sort_keys=True),
            encoding="utf-8",
        )

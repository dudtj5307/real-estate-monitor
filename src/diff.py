"""전일 스냅샷과 비교해 신규 / 가격변동 / 소진을 판정한다."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .naver import Article


@dataclass
class PriceChange:
    article: Article
    old_price: int
    old_rent: int


@dataclass
class Diff:
    new: list[Article] = field(default_factory=list)
    changed: list[PriceChange] = field(default_factory=list)
    gone: list[Article] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.new or self.changed or self.gone)


def compare(current: list[Article], previous: list[Article]) -> Diff:
    prev_by_id = {a.article_number: a for a in previous}
    cur_by_id = {a.article_number: a for a in current}

    diff = Diff()

    for article_id, article in cur_by_id.items():
        old = prev_by_id.get(article_id)
        if old is None:
            diff.new.append(article)
        elif old.price_key() != article.price_key():
            diff.changed.append(PriceChange(article, old.price, old.rent))

    for article_id, article in prev_by_id.items():
        if article_id not in cur_by_id:
            diff.gone.append(article)

    return diff


class Snapshot:
    """단지+거래유형 단위로 매물 목록을 보관하는 JSON 파일."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._data: dict[str, list[dict]] = {}
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                self._data = {}

    @staticmethod
    def key(complex_number: str, trade_type: str) -> str:
        return f"{complex_number}:{trade_type}"

    def exists(self) -> bool:
        return bool(self._data)

    def get(self, complex_number: str, trade_type: str) -> list[Article]:
        raw = self._data.get(self.key(complex_number, trade_type)) or []
        return [Article.from_dict(d) for d in raw]

    def set(self, complex_number: str, trade_type: str, articles: list[Article]) -> None:
        self._data[self.key(complex_number, trade_type)] = [a.to_dict() for a in articles]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=1, sort_keys=True),
            encoding="utf-8",
        )

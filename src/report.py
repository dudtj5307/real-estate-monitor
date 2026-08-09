"""텔레그램 메시지 텍스트 생성."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from .diff import Diff
from .naver import Article
from .state import KST

WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]
TELEGRAM_LIMIT = 4096


def fmt_price(manwon: int) -> str:
    """만원 단위 정수를 '10.50억' / '5,000만' 형태로."""
    if manwon <= 0:
        return "-"
    if manwon >= 10000:
        return f"{manwon / 10000:.2f}억"
    return f"{manwon:,}만"


def fmt_delta(manwon: int) -> str:
    sign = "+" if manwon > 0 else "-"
    return f"{sign}{fmt_price(abs(manwon))}"


def fmt_amount(article: Article) -> str:
    if article.rent:
        return f"{fmt_price(article.price)}/{fmt_price(article.rent)}"
    return fmt_price(article.price)


def fmt_count_delta(current: int, previous: int) -> str:
    d = current - previous
    if d > 0:
        return f"(▲{d})"
    if d < 0:
        return f"(▼{-d})"
    return "(—)"


def _loc(article: Article) -> str:
    """'201동 10/20층' 형태의 위치 표기."""
    dong = f"{article.dong}동 " if article.dong else ""
    floor = f"{article.floor}층" if article.floor else ""
    return f"{dong}{floor}".strip()


def _line(article: Article) -> str:
    head = f"{_loc(article)} · {fmt_amount(article)}"
    if article.realtor_count > 1:
        head += f" · 중개사{article.realtor_count}"
    return head


@dataclass
class TradeSection:
    """단지 하나의 거래유형 하나에 대한 결과."""

    trade_type: str
    articles: list[Article]
    diff: Diff
    # diff 를 어느 날 값과 비교했는가. 빈 값이면 기준이 없다(첫 수집).
    # 수집이 며칠 막히면 이게 '어제'가 아니라 며칠 전이 되므로 그대로 보여 준다.
    baseline_date: str = ""


def basis(baseline_date: str) -> str:
    if not baseline_date:
        return "첫 수집 — 전건 신규"
    return f"{baseline_date} 대비"


def _pyeong_block(group: int, articles: list[Article], diff: Diff,
                  prev_count: int) -> list[str]:
    ids = {a.article_number for a in articles}
    new = [a for a in diff.new if a.article_number in ids]
    changed = [c for c in diff.changed if c.article.article_number in ids]
    gone = [a for a in diff.gone if a.pyeong_group == group]

    prices = [a.price for a in articles if a.price > 0]
    if prices:
        span = f" · {fmt_price(min(prices))}~{fmt_price(max(prices))}"
    else:
        span = ""

    lines = [f"[{group}평대] {len(articles)}건 {fmt_count_delta(len(articles), prev_count)}{span}"]

    for a in new:
        lines.append(f"  🆕 {_line(a)}")
        if a.feature:
            lines.append(f"     {a.feature[:60]}")
    for c in changed:
        delta = c.article.price - c.old_price
        # 번호가 바뀐 재등록은 링크도 바뀌었다는 뜻이라 표시해 둔다
        mark = " ↻" if c.renumbered else ""
        lines.append(
            f"  💸 {_loc(c.article)} · "
            f"{fmt_price(c.old_price)} → {fmt_price(c.article.price)} "
            f"({fmt_delta(delta)}){mark}"
        )
    if gone:
        lines.append(f"  ❌ 소진 {len(gone)}건")
    if not (new or changed or gone):
        lines.append("     변동 없음")

    return lines


def _group_by_pyeong(articles: list[Article]) -> dict[int, list[Article]]:
    out: dict[int, list[Article]] = defaultdict(list)
    for a in articles:
        out[a.pyeong_group].append(a)
    return dict(out)


def build(complex_name: str, sections: list[TradeSection]) -> str:
    lines = [f"━━ {complex_name} ━━"]

    for section in sections:
        lines.append(f"💰 {section.trade_type}  ({basis(section.baseline_date)})")

        groups = _group_by_pyeong(section.articles)
        for group in sorted(groups):
            # 이전 건수 = 현재 건수 - 신규 + 소진
            ids = {a.article_number for a in groups[group]}
            new_n = len([a for a in section.diff.new if a.article_number in ids])
            gone_n = len([a for a in section.diff.gone if a.pyeong_group == group])
            prev_n = len(groups[group]) - new_n + gone_n
            lines.extend(_pyeong_block(group, groups[group], section.diff, prev_n))
            lines.append("")

        if not groups:
            lines.append("  조건에 맞는 매물 없음")
            lines.append("")

    return "\n".join(lines).rstrip()


def header(now: datetime | None = None) -> str:
    # 러너는 UTC 라 datetime.now() 를 쓰면 KST 아침 실행이 '어제' 날짜로 찍힌다.
    now = now or datetime.now(KST)
    return f"🏠 부동산 리포트 · {now:%Y-%m-%d} ({WEEKDAYS[now.weekday()]})"


def chunk(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """텔레그램 길이 제한에 맞춰 줄 단위로 분할."""
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    buf: list[str] = []
    size = 0
    for line in text.split("\n"):
        # 한 줄 자체가 한계를 넘으면 잘라낸다
        if len(line) > limit:
            line = line[: limit - 1]
        if size + len(line) + 1 > limit and buf:
            chunks.append("\n".join(buf))
            buf, size = [], 0
        buf.append(line)
        size += len(line) + 1
    if buf:
        chunks.append("\n".join(buf))
    return chunks

"""매일 아침 실행되는 진입점.

    python -m src.main                 # 수집 → 리포트 → 텔레그램 전송
    python -m src.main --dry-run       # 전송하지 않고 콘솔에만 출력
    python -m src.main --no-save       # 스냅샷을 갱신하지 않음 (테스트용)
"""

from __future__ import annotations

import argparse
import sys
import time
import traceback
from pathlib import Path

from . import filters, htmlgen, report
from .config import ComplexConfig, load
from .diff import Snapshot, compare
from .naver import COMPLEX_DELAY, Article, NaverClient
from .report import TradeSection
from .telegram import Telegram

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"
SNAPSHOT_PATH = ROOT / "data" / "snapshot.json"
HTML_PATH = ROOT / "docs" / "index.html"


def collect(client: NaverClient, cfg: ComplexConfig) -> dict[str, list[Article]]:
    """거래유형별로 필터링된 매물 목록을 돌려준다."""
    articles = client.fetch(cfg.number, cfg.trade_types)
    kept = filters.apply(articles, cfg)

    by_trade: dict[str, list[Article]] = {t: [] for t in cfg.trade_types}
    for a in kept:
        by_trade.setdefault(a.trade_type, []).append(a)
    return by_trade


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="네이버 부동산 → 텔레그램 리포트")
    parser.add_argument("--dry-run", action="store_true", help="전송하지 않고 콘솔 출력")
    parser.add_argument("--no-save", action="store_true", help="스냅샷을 갱신하지 않음")
    parser.add_argument("--config", default=str(CONFIG_PATH))
    args = parser.parse_args(argv)

    cfg = load(args.config)
    snapshot = Snapshot(SNAPSHOT_PATH)
    first_run = not snapshot.exists()
    client = NaverClient()

    blocks: list[str] = []
    entries: list[tuple[str, list[TradeSection]]] = []
    errors: list[str] = []
    succeeded = 0

    for idx, complex_cfg in enumerate(cfg.complexes):
        if idx > 0:
            time.sleep(COMPLEX_DELAY)

        print(f"[수집] {complex_cfg.name} ({complex_cfg.number}) ...", file=sys.stderr)
        try:
            by_trade = collect(client, complex_cfg)
        except Exception as exc:  # 한 단지 실패가 전체를 막지 않는다
            errors.append(f"{complex_cfg.name}: {exc}")
            traceback.print_exc()
            continue

        succeeded += 1
        sections: list[TradeSection] = []
        for trade_type in complex_cfg.trade_types:
            current = by_trade.get(trade_type, [])
            previous = snapshot.get(complex_cfg.number, trade_type)
            sections.append(
                TradeSection(
                    trade_type=trade_type,
                    articles=current,
                    diff=compare(current, previous),
                )
            )
            snapshot.set(complex_cfg.number, trade_type, current)

        blocks.append(report.build(complex_cfg.name, sections))
        entries.append((complex_cfg.name, sections))

    if succeeded == 0:
        # 전부 실패한 날은 스냅샷을 덮어쓰지 않는다.
        # 그대로 저장하면 다음 날 전 매물이 '신규'로 오보된다.
        message = report.header() + "\n\n⚠️ 수집에 모두 실패했습니다.\n" + "\n".join(errors)
        Telegram(cfg.chat_id).send(message)
        return 1

    parts = [report.header()]
    if first_run:
        parts.append("(첫 실행 — 전체 매물을 신규로 표시합니다)")
    parts.append("")
    parts.append("\n\n".join(blocks))
    if errors:
        parts.append("\n⚠️ 일부 단지 수집 실패:\n" + "\n".join(errors))

    text = "\n".join(parts)

    telegram = Telegram(cfg.chat_id)
    if args.dry_run:
        print(text)
    else:
        telegram.send_all(report.chunk(text))

    # HTML 대시보드는 항상 갱신한다 (전송 여부와 무관하게 최신 상태를 보고 싶으므로)
    HTML_PATH.parent.mkdir(parents=True, exist_ok=True)
    HTML_PATH.write_text(htmlgen.build(entries), encoding="utf-8")
    print(f"[생성] {HTML_PATH}", file=sys.stderr)

    if not args.no_save:
        snapshot.save()
        print(f"[저장] {SNAPSHOT_PATH}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

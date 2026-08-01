"""매일 아침 실행되는 진입점.

    python -m src.main                 # 수집 → 리포트 → 텔레그램 전송
    python -m src.main --dry-run       # 전송하지 않고 콘솔에만 출력
    python -m src.main --no-save       # 스냅샷을 갱신하지 않음 (테스트용)
    python -m src.main --skip-if-done  # 오늘 이미 성공했으면 아무것도 안 함
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
from .naver import COMPLEX_DELAY, Article, NaverClient, RateLimited
from .report import TradeSection
from .state import State
from .telegram import Telegram

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"
SNAPSHOT_PATH = ROOT / "data" / "snapshot.json"
STATE_PATH = ROOT / "data" / "state.json"
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
    parser.add_argument(
        "--skip-if-done", action="store_true",
        help="오늘 이미 성공했으면 즉시 종료 (하루 여러 번 예약 실행할 때)",
    )
    args = parser.parse_args(argv)

    state = State(STATE_PATH)
    if args.skip_if_done and state.done_today():
        print("[건너뜀] 오늘 이미 수집에 성공했습니다", file=sys.stderr)
        return 0

    cfg = load(args.config)
    snapshot = Snapshot(SNAPSHOT_PATH)
    first_run = not snapshot.exists()
    client = NaverClient()

    blocks: list[str] = []
    entries: list[tuple[str, list[TradeSection]]] = []
    errors: list[str] = []
    succeeded = 0
    rate_limited = 0

    for idx, complex_cfg in enumerate(cfg.complexes):
        if idx > 0:
            time.sleep(COMPLEX_DELAY)

        print(f"[수집] {complex_cfg.name} ({complex_cfg.number}) ...", file=sys.stderr)
        try:
            by_trade = collect(client, complex_cfg)
        except Exception as exc:  # 한 단지 실패가 전체를 막지 않는다
            if isinstance(exc, RateLimited):
                rate_limited += 1
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
        # 하루 여러 번 시도하므로 실패 알림은 하루 한 번만 보낸다
        if state.should_notify_failure():
            parts = [report.header(), "", "⚠️ 수집에 모두 실패했습니다.", *errors]
            if rate_limited == len(cfg.complexes):
                parts += [
                    "",
                    "네이버가 실행 IP 를 차단한 상태입니다(429). 코드 문제가 아니라",
                    "IP 단위 차단이라 헤더·간격 조정으로는 풀리지 않습니다.",
                    "오늘 남은 예약 실행이 다른 IP 로 다시 시도합니다.",
                    "계속되면 로컬 작업 스케줄러(scripts/run_daily.ps1)로 옮기세요.",
                ]
            Telegram(cfg.chat_id).send("\n".join(parts))
            state.mark_failure_notified()
        else:
            print("[알림 생략] 오늘 이미 실패를 알렸습니다", file=sys.stderr)
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
    HTML_PATH.write_text(
        htmlgen.build(entries, repo=cfg.repo, price_focus=cfg.price_focus),
        encoding="utf-8",
    )
    print(f"[생성] {HTML_PATH}", file=sys.stderr)

    if not args.no_save:
        snapshot.save()
        print(f"[저장] {SNAPSHOT_PATH}", file=sys.stderr)

        # 전 단지가 성공한 날만 '오늘 완료'로 찍는다. 일부만 됐다면 남은 예약 실행이
        # 다른 IP 로 나머지를 채울 기회를 남겨 둔다.
        if not errors:
            state.mark_success()
            print(f"[완료] {STATE_PATH}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

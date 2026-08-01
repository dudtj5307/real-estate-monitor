"""매일 아침 실행되는 진입점.

    python -m src.main                  # 수집 → 리포트 → 텔레그램 전송
    python -m src.main --dry-run        # 전송하지 않고 콘솔에만 출력
    python -m src.main --no-save        # 스냅샷을 갱신하지 않음 (테스트용)
    python -m src.main --skip-if-done   # 오늘 이미 성공한 단지는 건너뜀
    python -m src.main --test-telegram  # 텔레그램 설정만 확인하고 종료

종료 코드
    0  정상 (또는 오늘 할 일이 없어 건너뜀)
    1  예상 못한 실패
    2  실행 IP 가 네이버에 차단됨 — 예정된 다음 실행이 다른 IP 로 재시도한다.
       워크플로는 이걸 빨간 X 로 만들지 않는다 (daily.yml).
"""

from __future__ import annotations

import argparse
import sys
import time
import traceback
from pathlib import Path

from . import filters, htmlgen, report
from .config import ComplexConfig, Config, load
from .diff import Diff, Snapshot, compare
from .naver import COMPLEX_DELAY, Article, IPBlocked, NaverClient, RateLimited
from .report import TradeSection
from .state import State
from .telegram import Telegram, TelegramError

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"
SNAPSHOT_PATH = ROOT / "data" / "snapshot.json"
STATE_PATH = ROOT / "data" / "state.json"
HTML_PATH = ROOT / "docs" / "index.html"

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_BLOCKED = 2


def collect(client: NaverClient, cfg: ComplexConfig) -> dict[str, list[Article]]:
    """거래유형별로 필터링된 매물 목록을 돌려준다."""
    articles = client.fetch(cfg.number, cfg.trade_types)
    kept = filters.apply(articles, cfg)

    by_trade: dict[str, list[Article]] = {t: [] for t in cfg.trade_types}
    for a in kept:
        by_trade.setdefault(a.trade_type, []).append(a)
    return by_trade


def _from_snapshot(snapshot: Snapshot, cfg: ComplexConfig) -> list[TradeSection]:
    """이미 오늘 수집한 단지를 스냅샷에서 되살린다.

    다시 부르지 않는 이유가 요청 수 절약이므로, 대시보드에서 그 단지가 사라지지
    않게 저장된 값을 그대로 쓴다. 변동은 아침 리포트에서 이미 알렸으므로 비운다.
    """
    return [
        TradeSection(trade_type=t, articles=snapshot.get(cfg.number, t), diff=Diff())
        for t in cfg.trade_types
    ]


def _notify_failure(cfg: Config, state: State, errors: list[str], blocked: bool) -> None:
    """수집이 전부 실패한 날의 알림. 하루 한 번만 보낸다."""
    if not state.should_notify_failure():
        print("[알림 생략] 오늘 이미 실패를 알렸습니다", file=sys.stderr)
        return

    parts = [report.header(), "", "⚠️ 수집에 모두 실패했습니다.", *errors]
    if blocked:
        parts += [
            "",
            "네이버가 실행 IP 를 차단한 상태입니다(429). 코드 문제가 아니라",
            "IP 단위 차단이라 헤더·간격 조정으로는 풀리지 않습니다.",
            "오늘 남은 예약 실행이 다른 IP 로 다시 시도합니다.",
            "계속되면 로컬 작업 스케줄러(scripts/run_daily.ps1)로 옮기세요.",
        ]
    try:
        Telegram(cfg.chat_id).send("\n".join(parts))
    except TelegramError as exc:
        print(f"[오류] 실패 알림도 못 보냈습니다: {exc}", file=sys.stderr)
        return
    state.mark_failure_notified()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="네이버 부동산 → 텔레그램 리포트")
    parser.add_argument("--dry-run", action="store_true", help="전송하지 않고 콘솔 출력")
    parser.add_argument("--no-save", action="store_true", help="스냅샷을 갱신하지 않음")
    parser.add_argument("--config", default=str(CONFIG_PATH))
    parser.add_argument(
        "--skip-if-done", action="store_true",
        help="오늘 이미 수집한 단지는 건너뜀 (하루 여러 번 예약 실행할 때)",
    )
    parser.add_argument(
        "--test-telegram", action="store_true",
        help="텔레그램 설정을 확인하고 테스트 메시지를 보낸 뒤 종료",
    )
    args = parser.parse_args(argv)

    cfg = load(args.config)
    telegram = Telegram(cfg.chat_id)

    if args.test_telegram:
        return EXIT_OK if telegram.test() else EXIT_ERROR
    if not args.dry_run:
        # 미설정이면 리포트가 콘솔로만 나가고 조용히 끝난다. 먼저 크게 경고한다.
        telegram.warn_if_disabled()

    state = State(STATE_PATH)
    if args.skip_if_done and state.done_today():
        print("[건너뜀] 오늘 이미 수집에 성공했습니다", file=sys.stderr)
        return EXIT_OK

    snapshot = Snapshot(SNAPSHOT_PATH)
    first_run = not snapshot.exists()
    client = NaverClient()

    blocks: list[str] = []
    entries: list[tuple[str, list[TradeSection]]] = []
    errors: list[str] = []
    done_numbers: list[str] = []  # 이번에 새로 수집한 단지 (저장 후 상태에 기록)
    blocked = False       # 실행 IP 차단으로 남은 단지를 포기했다
    rate_limited = 0      # 429 로 실패한 단지 수
    skipped = 0
    fetched = 0

    for idx, complex_cfg in enumerate(cfg.complexes):
        # 아침에 성공한 단지는 다시 부르지 않는다. 요청이 줄면 새 IP 가
        # 차단에 걸릴 확률도 준다.
        if args.skip_if_done and state.complex_done_today(complex_cfg.number):
            print(f"[건너뜀] {complex_cfg.name} — 오늘 이미 수집됨", file=sys.stderr)
            entries.append((complex_cfg.name, _from_snapshot(snapshot, complex_cfg)))
            skipped += 1
            continue

        if fetched:
            time.sleep(COMPLEX_DELAY)

        print(f"[수집] {complex_cfg.name} ({complex_cfg.number}) ...", file=sys.stderr)
        try:
            by_trade = collect(client, complex_cfg)
        except IPBlocked as exc:
            # 첫 요청부터 막혔다 = 이 IP 로는 나머지 단지도 볼 것 없다.
            # 계속 두드리지 않고 끝낸다. 다음 예약 실행이 새 IP 로 받는다.
            blocked = True
            rate_limited += 1
            errors.append(f"{complex_cfg.name}: {exc}")
            remaining = len(cfg.complexes) - idx - 1
            print(f"[중단] 실행 IP 차단 — 남은 {remaining}개 단지를 건너뜁니다",
                  file=sys.stderr)
            break
        except Exception as exc:  # 한 단지 실패가 전체를 막지 않는다
            if isinstance(exc, RateLimited):
                rate_limited += 1
            errors.append(f"{complex_cfg.name}: {exc}")
            traceback.print_exc()
            continue

        fetched += 1
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
        # '오늘 끝났다'는 표시는 스냅샷을 실제로 저장한 뒤에 찍는다. 먼저 찍으면
        # 중간에 죽었을 때 다음 실행이 저장되지 않은 단지를 건너뛰어 버린다.
        done_numbers.append(complex_cfg.number)

    if fetched == 0:
        # 새로 받아온 게 없는 날은 스냅샷을 덮어쓰지 않는다.
        # 그대로 저장하면 다음 날 전 매물이 '신규'로 오보된다.
        if skipped and not errors:
            print("[건너뜀] 모든 단지가 오늘 이미 수집됐습니다", file=sys.stderr)
            if not args.no_save:
                state.mark_success()
            return EXIT_OK
        # 429 로만 실패했다면(또는 IP 차단으로 조기 종료했다면) 차단 안내를 붙인다
        all_rate_limited = blocked or rate_limited == len(errors)
        _notify_failure(cfg, state, errors, all_rate_limited)
        return EXIT_BLOCKED if all_rate_limited else EXIT_ERROR

    parts = [report.header()]
    if first_run:
        parts.append("(첫 실행 — 전체 매물을 신규로 표시합니다)")
    parts.append("")
    parts.append("\n\n".join(blocks))
    if errors:
        parts.append("\n⚠️ 일부 단지 수집 실패:\n" + "\n".join(errors))

    text = "\n".join(parts)

    send_failed = ""
    if args.dry_run:
        print(text)
    else:
        try:
            telegram.send_all(report.chunk(text))
        except TelegramError as exc:
            # 전송 실패로 스냅샷·대시보드까지 날리지 않는다. 수집은 성공했으므로
            # 저장은 마치고, 종료 코드로 실패를 알린다.
            send_failed = str(exc)
            print(f"[오류] {send_failed}", file=sys.stderr)

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

        for number in done_numbers:
            state.mark_complex_done(number)

        # 전 단지가 성공한 날만 '오늘 완료'로 찍는다. 일부만 됐다면 남은 예약 실행이
        # 다른 IP 로 나머지를 채울 기회를 남겨 둔다.
        if not errors:
            state.mark_success()
            print(f"[완료] {STATE_PATH}", file=sys.stderr)

    # 일부만 실패한 경우는 리포트에 실패 내역이 함께 실려 나갔으므로 성공으로 본다.
    # (남은 예약 실행이 빈 단지를 채운다 — state.mark_success 를 안 찍었다)
    return EXIT_ERROR if send_failed else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())

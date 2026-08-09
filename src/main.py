"""매일 아침 실행되는 진입점.

    python -m src.main                  # 수집 → 리포트 → 텔레그램 전송
    python -m src.main --dry-run        # 전송하지 않고 콘솔에만 출력
    python -m src.main --outbox         # 보내는 대신 data/outbox.json 에 적는다
    python -m src.main --no-save        # 스냅샷을 갱신하지 않음 (테스트용)
    python -m src.main --skip-if-done   # 오늘 이미 성공한 단지는 건너뜀
    python -m src.main --test-telegram  # 텔레그램 설정만 확인하고 종료

전달 방식은 셋 중 하나이고 우선순위가 있다: --dry-run > --outbox > 직접 전송.
라즈베리파이는 --outbox 로 돈다. 비밀값을 집 기기에 두지 않기 위해서이고,
전송은 push 를 감지한 notify.yml 이 맡는다 (DESIGN-PI.md §5.4).

종료 코드
    0  정상 (또는 오늘 할 일이 없어 건너뜀)
    1  예상 못한 실패
    2  실행 IP 가 네이버에 차단됨 — 실패로 취급하지 말 것. 집 IP 는 바뀌지 않으므로
       Pi 는 다음 요청 때 다시 시도하고, 그날 다른 IP 로 시도하는 경로는 watchdog
       폴백뿐이다. 호출자(run.sh · watchdog.yml)는 이걸 빨간 X 로 만들지 않는다.
"""

from __future__ import annotations

import argparse
import sys
import time
import traceback
from pathlib import Path

from . import filters, htmlgen, outbox, report
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
OUTBOX_PATH = ROOT / "data" / "outbox.json"
HTML_PATH = ROOT / "docs" / "index.html"

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_BLOCKED = 2


def collect(client: NaverClient, cfg: ComplexConfig) -> dict[str, list[Article]]:
    """거래유형별 매물 목록. **필터를 여기서 걸지 않는다.**

    필터 통과분만 스냅샷에 남기면 경계를 넘나든 매물이 '가격변동'이 아니라
    '소진 + 신규'로 오보된다 (DESIGN.md 2.0.1 — 10.8억 매물이 11.5억이 되면
    price_max 밖으로 나가 스냅샷에서 사라진다). 좁히기는 비교가 끝난 뒤에 한다.
    """
    articles = client.fetch(cfg.number, cfg.trade_types)

    by_trade: dict[str, list[Article]] = {t: [] for t in cfg.trade_types}
    for a in articles:
        by_trade.setdefault(a.trade_type, []).append(a)
    return by_trade


def _section(snapshot: Snapshot, cfg: ComplexConfig, trade_type: str,
             articles: list[Article]) -> TradeSection:
    """전체 매물을 기준과 비교한 뒤, 표시용으로만 설정 범위로 좁힌다."""
    base = snapshot.baseline(cfg.number, trade_type)
    diff = compare(articles, base.articles)

    kept = filters.apply(articles, cfg)
    ids = {a.article_number for a in kept}
    return TradeSection(
        trade_type=trade_type,
        articles=kept,
        diff=Diff(
            new=[a for a in diff.new if a.article_number in ids],
            changed=[c for c in diff.changed if c.article.article_number in ids],
            gone=filters.apply(diff.gone, cfg),
        ),
        baseline_date=base.date,
    )


def _from_snapshot(snapshot: Snapshot, cfg: ComplexConfig) -> list[TradeSection]:
    """이미 오늘 수집한 단지를 스냅샷에서 되살린다.

    다시 부르지 않는 이유가 요청 수 절약이므로, 대시보드에서 그 단지가 사라지지
    않게 저장된 값을 그대로 쓴다. 비교 기준은 날짜가 바뀔 때만 굴리므로
    (diff.Snapshot) 오늘의 신규·변동 표시도 그대로 재현된다 — 텔레그램에는
    이 단지를 다시 싣지 않으므로 중복 알림은 나가지 않는다.
    """
    return [
        _section(snapshot, cfg, t, snapshot.latest(cfg.number, t))
        for t in cfg.trade_types
    ]


def _notify_failure(cfg: Config, state: State, errors: list[str], blocked: bool,
                    use_outbox: bool = False) -> None:
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
            "오늘 남은 요청(12:30 · 14:00 폴백)이 다시 시도합니다.",
            "",
            "며칠 연속이면 집 IP 가 실제로 찍힌 것입니다. 그때는 config.yaml 의",
            "단지 수를 줄이거나 며칠 쉬었다 재개하세요 — 호출 간격은 이미 실측",
            "하한이라 더 늘려도 이득이 크지 않습니다.",
        ]
    try:
        if use_outbox:
            # 실패 알림도 같은 경로를 탄다. Pi 에는 토큰이 없다.
            path = outbox.write(OUTBOX_PATH, "failure", "\n".join(parts))
            print(f"[대기] 실패 알림을 {path} 에 적었습니다", file=sys.stderr)
        else:
            Telegram(cfg.chat_id).send("\n".join(parts))
    except (TelegramError, OSError) as exc:
        print(f"[오류] 실패 알림도 못 보냈습니다: {exc}", file=sys.stderr)
        return
    # 알림이 push 되지 못하면 state.json 도 함께 남지 않는다. 다음 실행이
    # reset --hard 로 되돌아가 같은 알림을 다시 시도하므로 어긋나지 않는다.
    state.mark_failure_notified()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="네이버 부동산 → 텔레그램 리포트")
    parser.add_argument("--dry-run", action="store_true", help="전송하지 않고 콘솔 출력")
    parser.add_argument(
        "--outbox", action="store_true",
        help="전송하지 않고 data/outbox.json 에 적는다 (GitHub Actions 가 보낸다)",
    )
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
    # --outbox 는 애초에 토큰을 기대하지 않는다 (전송은 Actions 가 한다).
    if not args.dry_run and not args.outbox:
        # 미설정이면 리포트가 콘솔로만 나가고 조용히 끝난다. 먼저 크게 경고한다.
        telegram.warn_if_disabled()

    state = State(STATE_PATH)
    if args.skip_if_done and state.done_today():
        print("[건너뜀] 오늘 이미 수집에 성공했습니다", file=sys.stderr)
        return EXIT_OK

    snapshot = Snapshot(SNAPSHOT_PATH)
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
            # 비교가 먼저다 — set() 이 기준을 굴려 버리므로 순서를 바꾸면 안 된다
            sections.append(_section(snapshot, complex_cfg, trade_type, current))
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
        _notify_failure(cfg, state, errors, all_rate_limited, use_outbox=args.outbox)
        return EXIT_BLOCKED if all_rate_limited else EXIT_ERROR

    # '무엇과 비교한 결과인가'는 거래유형 줄마다 붙는다 (report.basis).
    # 단지마다 마지막 성공일이 다를 수 있어 전역 문구로는 정확히 말할 수 없다.
    parts = [report.header(), ""]
    parts.append("\n\n".join(blocks))
    if errors:
        parts.append("\n⚠️ 일부 단지 수집 실패:\n" + "\n".join(errors))

    text = "\n".join(parts)

    send_failed = ""
    if args.dry_run:
        print(text)
    else:
        try:
            if args.outbox:
                # 여기서 보내지 않는다. push 를 감지한 notify.yml 이 secret 으로 보낸다.
                path = outbox.write(OUTBOX_PATH, "report", text)
                print(f"[대기] 리포트를 {path} 에 적었습니다 — 전송은 Actions 가 합니다",
                      file=sys.stderr)
            else:
                telegram.send_all(report.chunk(text))
        except (TelegramError, OSError) as exc:
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

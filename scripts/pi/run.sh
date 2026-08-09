#!/bin/bash
# 수집 1회 — 인터넷 대기 → 최신 코드 동기화 → 수집 → push.
#
# 이 Pi 에는 비밀값이 없다. 리포트는 텔레그램으로 직접 보내지 않고 data/outbox.json
# 으로 push 하며, 전송은 그 push 를 감지한 notify.yml 이 맡는다 (DESIGN-PI.md §5.4).
# 따라서 push 가 실패하면 그날 알림도 없다 — 아래 ④ 의 재시도가 그래서 중요하다.
#
# 호출자는 poll.sh 이고, 수동 실행도 지원한다. 수동 실행 시에는 poll.sh 가 잡아 주던
# 잠금이 없으므로 호출하는 쪽에서 감싼다:
#
#     flock -n /tmp/naver-monitor.lock ./scripts/pi/run.sh --skip-if-done
#
# 여기서 flock 을 잡지 않는 이유: poll.sh 가 잠금을 쥔 채 exec 로 넘어오므로,
# 같은 파일을 다시 열어 flock -n 하면 자기 자신과 충돌해 아무것도 실행되지 않는다.
#
# set -e 를 쓰지 않는다. src.main 의 종료코드 2(네이버 차단)는 실패가 아니라 예상된
# 결과이고, 여기서 죽으면 그 사실을 기록한 state.json 을 push 하지 못한다.
set -uo pipefail

cd "$(dirname "$0")/../.." || exit 1

PROBE_URL=https://api.github.com/zen
NET_TIMEOUT=180     # 인터넷 대기 상한(초). 무한 대기 금지 — 다음 요청이 재시도한다

log() { printf '[run] %s\n' "$*"; }

# ── ① 진짜 인터넷 도달 확인 ───────────────────────────────────────────────
# network-online.target 은 링크가 올라온 것만 보장한다. Wi-Fi 는 링크가 떠도 DNS 가
# 늦게 준비되는 일이 흔해서, 이름이 풀리고 응답이 올 때까지 기다린다.
deadline=$((SECONDS + NET_TIMEOUT))
until curl -sf --max-time 5 "$PROBE_URL" >/dev/null; do
    if [ "$SECONDS" -ge "$deadline" ]; then
        # 마커는 poll.sh 가 이미 전진시켰으므로 이 요청은 여기서 소실된다.
        # at-most-once 의 대가이고, 12:30 요청이나 watchdog 폴백이 덮는다.
        log "인터넷 도달 실패 — ${NET_TIMEOUT}초 기다린 뒤 포기한다"
        exit 1
    fi
    sleep 5
done

# ── ② GitHub 가 정본 ──────────────────────────────────────────────────────
# Pi 는 데이터 생산자지 편집자가 아니다. 로컬 변경(push 못 한 잔여물, 폴백과의 충돌)은
# 버리고 origin/main 에서 시작한다. 이 한 줄이 폴백이 도는 날의 자가 치유를 담당한다.
git fetch --quiet origin || log "git fetch 실패 — 마지막으로 받아 둔 origin/main 으로 계속한다"
if ! git reset --quiet --hard origin/main; then
    log "git reset 실패 — 저장소 상태를 확인하라"
    exit 1
fi

# ── ③ 수집 ────────────────────────────────────────────────────────────────
# --outbox 는 항상 붙인다. Pi 에는 텔레그램 토큰이 없고, 리포트는 data/outbox.json
# 으로 push 되어 notify.yml 이 보낸다. 뒤따르는 인자는 poll.sh 가 결정한다
# (예약이면 --skip-if-done). 수동 점검에서 --dry-run 을 주면 그쪽이 우선한다.
python3 -m src.main --outbox "$@"
code=$?
case "$code" in
    0) log "수집 완료" ;;
    2) log "네이버가 집 IP 를 차단했다(429). 실패가 아니며 다음 요청이 재시도한다" ;;
    *) log "수집이 종료코드 $code 로 끝났다" ;;
esac

# ── ④ 결과 push ───────────────────────────────────────────────────────────
# 차단·실패로 끝났어도 돌려야 한다. 실패 알림 기록(state.json)이 push 돼야
# 12:30 요청이 같은 알림을 또 보내지 않는다.
git add data/ docs/
if git diff --cached --quiet; then
    log "변경 없음 — 커밋하지 않는다"
else
    # 커밋 신원을 -c 로 준다. Pi 의 전역 git 설정에 의존하지 않기 위해서다.
    git -c user.name="raspberrypi" -c user.email="pi@local" \
        commit -q -m "chore: 매물 스냅샷·대시보드 갱신 $(TZ=Asia/Seoul date +%F)"

    # watchdog 폴백이 같은 파일을 건드렸을 수 있다. rebase 후 1회만 재시도한다.
    if ! git push -q origin HEAD:main; then
        log "push 실패 — rebase 후 재시도"
        if ! { git pull --rebase -q origin main && git push -q origin HEAD:main; }; then
            log "push 재시도 실패 — 다음 실행의 reset --hard 로 버려진다 (그날 변경 1회 누락)"
            # 수집 자체가 정상이었다면 여기서 실패를 드러낸다. journalctl 에 남고
            # systemd 가 유닛을 failed 로 표시해야 조용히 묻히지 않는다.
            [ "$code" -eq 0 ] && code=1
        fi
    fi
fi

exit "$code"

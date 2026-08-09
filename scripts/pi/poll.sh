#!/bin/bash
# GitHub Actions 의 "갱신 요청" 실행을 감지해 수집을 돌린다. systemd timer 가 2분마다 부른다.
#
# 요청을 이벤트가 아니라 **상태**로 다룬다 — 마지막으로 처리한 workflow run id 를
# 저장하고 최신 run id 와 비교한다. 그래야 Pi 가 꺼져 있던 동안의 요청도 복귀 후에
# 보이고, 나중에 GPIO 웨이크 장치를 붙여도 프로토콜이 바뀌지 않는다 (DESIGN-PI.md §2.3).
set -uo pipefail

cd "$(dirname "$0")/../.." || exit 1

STATE_DIR=/var/lib/naver-monitor
MARKER="$STATE_DIR/last-request"
ETAG_FILE="$STATE_DIR/last-etag"
HEADERS=/tmp/naver-monitor.headers    # ETag 를 뽑기 위한 응답 헤더 덤프
LOCK=/tmp/naver-monitor.lock
WORKFLOW=refresh-request.yml

log() { printf '[poll] %s\n' "$*"; }

# 안정 상태(마커 = 최신 run id)에서만 ETag 를 남긴다. 이 순서가 중요하다 —
# 처리하기 전에 저장하면 다음 폴링이 304 를 받아 그 요청을 영영 못 본다.
save_etag() {
    local tag
    tag=$(sed -n 's/^[Ee][Tt][Aa][Gg]: *//p' "$HEADERS" 2>/dev/null | tr -d '\r' | tail -n 1)
    if [ -n "$tag" ]; then
        printf '%s\n' "$tag" > "$ETAG_FILE"
    fi
}

# 저장소 이름은 config.yaml 이 단일 출처다. 대시보드 버튼(htmlgen.py)이 가리키는
# 저장소와 폴링 대상이 갈라지지 않게 하려는 것이다. 하드코딩하지 말 것.
REPO=$(python3 -c "import yaml;print(yaml.safe_load(open('config.yaml'))['site']['repo'])") || exit 1
if [ -z "$REPO" ]; then
    log "config.yaml 의 site.repo 가 비어 있다"
    exit 1
fi

# ── ① 수집이 이미 돌고 있으면 물러난다 ────────────────────────────────────
# 마커를 건드리지 않고 나가므로 요청이 보존되고, 다음 폴링이 집어간다.
exec 9>"$LOCK" || exit 1
if ! flock -n 9; then
    log "수집이 진행 중 — 이번 폴링은 건너뛴다"
    exit 0
fi

# ── ② 최신 실행 1건 조회 ──────────────────────────────────────────────────
# public 저장소라 인증이 필요 없다. 미인증 한도는 IP 당 시간 60회이고 2분 폴링이
# 시간 30회라 여유가 있지만, 집 IP 는 다른 기기와 공유되므로 ETag 로 아낀다.
# (304 는 한도에서 차감되지 않는다)
api="https://api.github.com/repos/$REPO/actions/workflows/$WORKFLOW/runs?per_page=1"

curl_args=(-sS --max-time 15 -D "$HEADERS" -w '%{http_code}'
           -H "Accept: application/vnd.github+json"
           -H "X-GitHub-Api-Version: 2022-11-28")

etag=$(cat "$ETAG_FILE" 2>/dev/null) || etag=""
if [ -n "$etag" ]; then
    curl_args+=(-H "If-None-Match: $etag")
fi

# 네트워크·API 실패 시 마커를 건드리지 않고 그냥 나간다. 다음 폴링이 재시도한다.
if ! out=$(curl "${curl_args[@]}" "$api"); then
    log "API 호출 실패 — 마커를 그대로 두고 다음 폴링에서 재시도한다"
    exit 0
fi

status=${out: -3}       # -w 로 붙인 상태코드가 본문 뒤에 온다
body=${out:0:${#out}-3}

case "$status" in
    304) exit 0 ;;      # 마지막으로 확인한 실행에서 변한 게 없다
    200) ;;
    *)
        log "API 응답 $status — 마커를 그대로 둔다"
        exit 0
        ;;
esac

run_id=$(printf '%s' "$body" | jq -r '.workflow_runs[0].id // empty')
event=$(printf '%s' "$body" | jq -r '.workflow_runs[0].event // empty')
if [ -z "$run_id" ]; then
    log "실행 기록이 없다 — $WORKFLOW 이 한 번도 돌지 않았는지 확인하라"
    exit 0
fi

if ! mkdir -p "$STATE_DIR"; then
    log "$STATE_DIR 를 만들 수 없다 — 설치 가이드의 소유권 설정을 확인하라"
    exit 1
fi

# ── ③ 최초 설치 ───────────────────────────────────────────────────────────
# 지금 것을 처리한 것으로 표시만 하고 끝낸다. 설치하자마자 요청도 없는데 네이버를
# 두드리지 않게 하려는 것이다. 첫 수집은 다음 요청부터.
if [ ! -f "$MARKER" ]; then
    echo "$run_id" > "$MARKER"
    log "마커 초기화: run=$run_id (첫 수집은 다음 요청부터)"
    save_etag
    exit 0
fi

if [ "$(cat "$MARKER")" = "$run_id" ]; then
    save_etag
    exit 0
fi

# ── ④ 마커를 처리 *전에* 전진시킨다 (at-most-once) ────────────────────────
# 수집이 실패해도 2분마다 재시도하며 네이버를 두드리지 않기 위해서다.
# 놓친 요청은 12:30 요청이나 watchdog 폴백이 덮는다 (DESIGN-PI.md §2.3).
if ! echo "$run_id" > "$MARKER"; then
    # SD 카드가 읽기전용으로 떨어진 경우 등. 여기서 멈추지 않으면 마커가 남지 않아
    # 2분마다 같은 요청으로 수집을 반복하게 된다.
    log "마커를 쓸 수 없다 — 수집하지 않는다"
    exit 1
fi

# ── ⑤ 트리거 종류로 플래그 결정 ───────────────────────────────────────────
# 예약은 오늘 이미 성공한 단지를 건너뛰고, 버튼(수동)은 언제나 새로 수집한다.
flags=()
if [ "$event" = "schedule" ]; then
    flags+=(--skip-if-done)
fi

log "요청 감지: run=$run_id event=$event → 수집 시작"

# 헤더 덤프는 여기까지만 쓴다. exec 뒤에는 트랩이 돌지 않으므로 직접 지운다.
rm -f "$HEADERS"

# exec 로 넘겨야 fd 9 의 잠금이 수집이 끝날 때까지 유지된다.
exec ./scripts/pi/run.sh "${flags[@]}"

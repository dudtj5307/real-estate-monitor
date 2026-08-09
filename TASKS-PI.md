# 작업 분해 — 라즈베리파이 워커 전환

> 설계 근거: [DESIGN-PI.md](DESIGN-PI.md). 이 문서는 **다른 에이전트가 각 작업을
> 독립적으로 집어갈 수 있도록** 필요한 맥락을 작업마다 중복해서 적었습니다.
> 작업 전 §공통 컨텍스트만 읽으면 됩니다.

---

## 공통 컨텍스트 (모든 작업자 필독)

**무엇을 만드나.** 네이버 부동산 매물을 수집해 텔레그램으로 알리고 GitHub Pages
대시보드를 갱신하는 도구입니다. 지금은 GitHub Actions 공용 러너에서 도는데,
네이버가 러너 IP 를 차단해서(실측) **집 라즈베리파이 4B 로 실행 위치를 옮깁니다.**

**새 구조 한 줄 요약.**
GitHub Actions 가 "갱신 요청"을 발행하고, 상시 가동 중인 Pi 가 2분마다 그 요청을
폴링해 수집한 뒤 결과를 GitHub 으로 push 합니다. 인바운드 연결은 없습니다.

```
Actions 예약(KST 08:30/12:30) ─┐
대시보드 "지금 갱신" 버튼      ─┴→ refresh-request.yml 실행  (실행 기록 자체가 신호)
                                            │  Actions API (public, 인증 불필요)
                        Pi: poll.sh (2분) ──┘
                             └→ run.sh → python -m src.main --outbox → git push
                                            │
                        notify.yml (outbox 변경 push) → 텔레그램 전송
                        watchdog.yml (KST 14:00) → 미수집 시 경보 + 폴백 수집
```

**반드시 지킬 불변식 5가지.**

1. **요청은 이벤트가 아니라 상태다.** Pi 는 "마지막으로 처리한 workflow run id"를
   저장하고 최신 run id 와 비교합니다. 그래야 Pi 가 꺼져 있던 동안의 요청도
   복귀 후에 보이고, 나중에 GPIO 웨이크 장치를 붙여도 프로토콜이 안 바뀝니다.
2. **마커는 수집 *전에* 전진시킨다** (at-most-once). 수집이 실패해도 2분마다
   재시도하며 네이버를 두드리지 않게 하려는 것입니다. 놓친 요청은 12:30 트리거나
   watchdog 폴백이 덮습니다.
3. **평상시 저장소에 쓰는 주체는 Pi 하나뿐이다.** 요청을 커밋으로 만들지 마세요.
   폴백이 도는 날만 예외이고, Pi 가 매 실행 `git reset --hard origin/main` 으로
   받아가므로 자가 치유됩니다.
4. **네이버 호출 페이싱을 건드리지 말 것.** `src/naver.py` 의 `WARMUP_DELAY=8`,
   `COMPLEX_DELAY=25`, `PAGE_DELAY=25` 는 실측으로 정해진 값입니다.
5. **Pi 에는 비밀값을 두지 않는다.** 텔레그램 토큰은 저장소 Secret 에만 있습니다.
   Pi 는 리포트를 `data/outbox.json` 에 적어 push 하고, 전송은 `notify.yml` 이
   합니다 (DESIGN-PI.md §5.4). Pi 쪽 어디에도 토큰을 요구하는 코드를 넣지 마세요.
   그 결과 **push 가 알림의 유일한 경로**입니다 — push 실패는 그날 알림 실패입니다.

**기존 코드에서 알아야 할 것.**

| 항목 | 내용 |
|---|---|
| 진입점 | `python -m src.main` |
| 주요 플래그 | `--skip-if-done` (오늘 이미 성공한 단지 건너뜀), `--outbox` (전송 대신 파일로 — Pi 는 항상 이걸 쓴다), `--dry-run` (콘솔 출력만), `--no-save`, `--test-telegram` |
| 전달 우선순위 | `--dry-run` > `--outbox` > 직접 전송 |
| 종료 코드 | `0` 정상 / `1` 예상 못한 실패 / **`2` 네이버가 실행 IP 를 차단** (실패로 취급하지 말 것) |
| 상태 파일 | `data/state.json` — `last_success`, `complexes`, `last_failure_notice` (KST 날짜) |
| 산출물 | `data/snapshot.json`, `data/outbox.json`, `docs/index.html` |
| 비밀값 | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` — **저장소 Secret 에만**. Pi 에는 없음 |
| 저장소 이름 | `config.yaml` 의 `site.repo` (`dudtj5307/real-estate-monitor`). **하드코딩 금지, 여기서 읽을 것** |
| 저장소 공개 여부 | **public** — 비밀값을 파일로 커밋하지 말 것 |

**환경.** Pi 는 Raspberry Pi 4B / Bookworm / Wi-Fi / 상시 가동.
개발 머신은 Windows + PowerShell 이라 Pi 쉘 스크립트는 실기 테스트가 불가합니다.
정적 검증(`shellcheck`, `yamllint`, 코드 리뷰)까지가 각 작업의 완료 기준입니다.

**문서는 한국어**로 씁니다. 기존 코드 주석 스타일(왜 그렇게 했는지를 설명)을 따르세요.

---

## 의존 관계

```
T1  run.sh ✅ ─┐
               ├─→ T3 systemd ✅ ─→ T8 설치 가이드 ✅ ─→ T9 문서 갱신
T2  poll.sh ✅ ┘                      ▲
T12 notify.yml ✅ ────────────────────┤
T4  refresh-request.yml ✅ ───────────┤
T5  watchdog.yml (+daily.yml 제거) ───┘
T6  버튼 링크          (T4 이후)
T7  문구·정책 수정     (독립)
T10 (선택) 버튼 실행 텔레그램 억제   (T2 이후)
T11 (향후) GPIO 웨이크              (전부 이후)
```

남은 것은 **T5·T6·T7·T9** 이고 T5·T7 은 서로 독립이라 병렬로 진행 가능합니다.
**T5 와 T6 은 반드시 같이 머지**하세요 — T5 가 `daily.yml` 을 지우는데 대시보드
버튼(T6 이전)이 그 파일을 가리키고 있어 따로 머지하면 버튼이 404 가 됩니다.

⚠️ T8(설치 가이드)은 watchdog 이 있다는 전제로 "Pi 가 죽어도 14:00 에 경보가
온다"고 적어 두었습니다. **T5 가 머지되기 전에 Pi 를 설치하면 그 안전망이 없습니다.**

---

## T1 — Pi 수집 실행 래퍼 `scripts/pi/run.sh` ✅ 완료

> **구현 결과 — 초안에서 바뀐 점**
> - 네트워크 대기를 `seq 1 36` 루프 대신 `SECONDS` 데드라인으로. curl 타임아웃이
>   겹치면 초안은 최악 6분까지 갈 수 있어 수용 기준을 못 지켰습니다.
> - push 재시도까지 실패하면 수집이 정상(0)이었을 때 종료코드를 1 로 올립니다.
>   0 으로 끝나면 "그날 알림·데이터 누락"이 아무 데도 안 남습니다. 2 는 보존합니다.
> - `python3 -m src.main --outbox "$@"` — Pi 에는 토큰이 없습니다 (불변식 5).
> - 비밀값 관련 코드 없음. `/etc/naver-monitor.env` 는 더 이상 쓰지 않습니다.

**목적.** 한 번의 수집을 처음부터 끝까지(네트워크 대기 → 최신 코드 동기화 → 수집 →
push) 수행하는 스크립트. 호출자는 T2 의 `poll.sh` 이고, 수동 실행도 지원합니다.

**산출물.** `scripts/pi/run.sh` (실행 권한 필요 — `git update-index --chmod=+x`)

**설계 초안** (그대로 쓰지 말고 검토·보완할 것)

```bash
#!/bin/bash
# 수집 1회. poll.sh 가 호출하며, 수동 실행도 가능하다.
#   ./scripts/pi/run.sh --skip-if-done
set -uo pipefail
cd "$(dirname "$0")/../.."

# ① 진짜 인터넷 도달 확인 — network-online.target 은 링크만 보장한다.
#    Wi-Fi 는 링크가 떠도 DNS 가 늦게 준비되는 경우가 흔하다.
for _ in $(seq 1 36); do
    curl -sf --max-time 5 https://api.github.com/zen >/dev/null && break
    sleep 5
done

# ② GitHub 가 정본. Pi 는 데이터 생산자지 편집자가 아니므로 로컬 변경은 버린다.
git fetch --quiet origin && git reset --quiet --hard origin/main

# ③ 수집. 종료코드 2 = 네이버 차단이며 실패가 아니다.
python3 -m src.main "$@"
code=$?

# ④ 결과 push. 폴백이 같은 파일을 건드렸을 수 있어 rebase 후 1회 재시도.
git add data/ docs/
if ! git diff --cached --quiet; then
    git -c user.name="raspberrypi" -c user.email="pi@local" \
        commit -q -m "chore: 매물 스냅샷·대시보드 갱신 $(date +%F)"
    git push -q || { git pull --rebase -q && git push -q; }
fi
exit $code
```

**주의**

- `set -e` 를 쓰지 마세요. 종료코드 2 에서 스크립트가 죽으면 push 단계를 못 탑니다.
- **`flock` 은 여기 넣지 않습니다.** T2 가 잠금을 잡은 채 `exec` 하므로, 여기서 같은
  파일을 다시 열어 `flock -n` 하면 자기 자신과 충돌해 실행되지 않습니다.
  수동 실행 시에는 호출자가 감싸도록 T8 가이드에 적습니다:
  `flock -n /tmp/naver-monitor.lock ./scripts/pi/run.sh`
- 인자(`"$@"`)를 그대로 `src.main` 에 넘겨야 T2 가 플래그를 제어할 수 있습니다.

**수용 기준**

- [x] `shellcheck scripts/pi/run.sh` 무경고 (`--severity=style`)
- [x] `src.main` 이 2 를 반환해도 push 단계가 실행되고, 최종 종료코드가 2 로 보존된다
- [x] 변경이 없으면 빈 커밋을 만들지 않는다
- [x] 네트워크 대기 루프가 최대 3분에서 끝난다 (무한 대기 금지)

---

## T2 — Pi 요청 폴러 `scripts/pi/poll.sh` ✅ 완료

> **구현 결과 — 초안에서 바뀐 점**
> - ETag 조건부 요청을 넣었습니다. 단, **"마커 = 최신 run id" 인 안정 상태에서만
>   ETag 를 저장**합니다. 처리 전에 저장하면 다음 폴링이 304 를 받아 그 요청을
>   영영 못 봅니다.
> - 마커 쓰기가 실패하면(SD 읽기전용 등) 수집하지 않고 종료합니다. 그러지 않으면
>   2분마다 같은 요청으로 네이버를 두드립니다.
> - HTTP 상태를 직접 확인합니다 (`-w '%{http_code}'`). `curl -f` 는 304 를 성공으로
>   보고 빈 본문을 주기 때문에 304 와 이상 응답이 구분되지 않습니다.

**목적.** GitHub Actions 의 요청 워크플로 실행을 감지해 T1 을 호출합니다.
이 파이프라인의 핵심이므로 §공통 컨텍스트의 불변식 1·2를 반드시 지키세요.

**산출물.** `scripts/pi/poll.sh` (실행 권한)

**설계 초안**

```bash
#!/bin/bash
# GitHub Actions 의 "갱신 요청" 실행을 감지해 수집을 돌린다. 2분마다 호출된다.
set -uo pipefail
cd "$(dirname "$0")/../.."

MARKER=/var/lib/naver-monitor/last-request
WORKFLOW=refresh-request.yml
REPO=$(python3 -c "import yaml;print(yaml.safe_load(open('config.yaml'))['site']['repo'])")

# ① 수집이 이미 돌고 있으면 마커를 건드리지 않고 물러난다 → 요청이 보존된다
exec 9>/tmp/naver-monitor.lock
flock -n 9 || exit 0

# ② 최신 실행 1건 조회. public 저장소라 인증이 필요 없다.
api="https://api.github.com/repos/$REPO/actions/workflows/$WORKFLOW/runs?per_page=1"
json=$(curl -sf --max-time 15 -H "Accept: application/vnd.github+json" "$api") || exit 0

run_id=$(printf '%s' "$json" | jq -r '.workflow_runs[0].id // empty')
event=$(printf '%s'  "$json" | jq -r '.workflow_runs[0].event // empty')
[ -n "$run_id" ] || exit 0

mkdir -p "$(dirname "$MARKER")"

# ③ 최초 설치: 지금 것을 처리한 것으로 표시만 하고 끝낸다 (설치 직후 불필요한 수집 방지)
if [ ! -f "$MARKER" ]; then
    echo "$run_id" > "$MARKER"
    exit 0
fi

[ "$(cat "$MARKER")" = "$run_id" ] && exit 0

# ④ 처리 *전에* 전진시킨다 — 실패를 2분마다 재시도하며 네이버를 두드리지 않게
echo "$run_id" > "$MARKER"

# ⑤ 예약 트리거는 오늘 이미 됐으면 건너뛰고, 버튼(수동)은 언제나 새로 수집한다
flags=()
[ "$event" = "schedule" ] && flags+=(--skip-if-done)

exec ./scripts/pi/run.sh "${flags[@]}"
```

**주의**

- `exec` 로 넘겨야 fd 9 의 잠금이 수집이 끝날 때까지 유지됩니다.
- `curl -f` 실패(네트워크·API 한도) 시 **마커를 건드리지 말고** 그냥 종료하세요.
  다음 폴링이 재시도합니다.
- `/var/lib/naver-monitor/` 는 T3 설치 단계에서 `pi` 소유로 미리 만들어야 합니다.
- ETag 조건부 요청(`If-None-Match`)은 선택 최적화입니다. 넣으면 304 가 API 한도에서
  차감되지 않습니다. 넣을 경우 ETag 도 마커 옆에 저장하세요.

**수용 기준**

- [x] `shellcheck scripts/pi/poll.sh` 무경고 (`--severity=style`)
- [x] 같은 run id 를 두 번 처리하지 않는다
- [x] 잠금 획득 실패 시 마커가 변하지 않는다 (요청 보존) — Windows 에 `flock` 이 없어
      스텁으로 분기만 검증. 실제 잠금은 Pi 에서 확인 필요
- [x] API 실패 시 마커가 변하지 않는다 (네트워크 실패 · 5xx 둘 다)
- [x] `event=workflow_dispatch` 면 `--skip-if-done` 이 붙지 않는다
- [x] 마커 파일이 없을 때 수집을 돌리지 않는다
- [x] (추가) ETag 조건부 요청 — 안정 상태에서만 저장해 304 가 요청을 가리지 않는다

---

## T3 — systemd 유닛 ✅ 완료

> **구현 결과 — 초안에서 바뀐 점**
> - `StateDirectory=naver-monitor` 를 넣었습니다. systemd 가 `/var/lib/naver-monitor`
>   를 `User=` 소유로 만들어 주므로 설치할 때 손으로 `mkdir` 할 필요가 없습니다
>   (수동으로 `poll.sh` 를 먼저 돌려 보는 경우만 T8 에 남겨 뒀습니다).
> - 타이머를 `OnUnitActiveSec` 이 아니라 **`OnUnitInactiveSec=2min`** 으로 했습니다.
>   전자면 수집이 3분 걸렸을 때 밀린 폴링이 끝나자마자 곧바로 한 번 더 돕니다.
> - `PrivateTmp=` 를 켜지 말라는 경고를 유닛에 적었습니다. `/tmp/naver-monitor.lock`
>   이 서비스와 수동 실행 사이의 유일한 상호 배제 수단이라, 켜면 서로 다른 `/tmp` 를
>   보게 돼 동시 수집이 가능해집니다.
> - `Wants=time-sync.target` 은 뺐습니다. 기다려 주는 유닛을 끌어오지 못해 실질 효과가
>   없습니다. 실제 완충은 타이머의 `OnBootSec=2min` 입니다.
> - `SyslogIdentifier=naver-monitor` 추가 — `journalctl -t` 로 한 번에 봅니다.

**목적.** 폴러를 2분마다 돌립니다.

> ⚠️ **환경파일은 만들지 마세요.** 초안에 있던 `naver-monitor.env.example` 과
> `EnvironmentFile=` 은 삭제됐습니다. Pi 에는 비밀값이 없습니다 (불변식 5).
> `PYTHONIOENCODING` 같은 값이 필요하면 유닛에 `Environment=` 로 직접 적으세요.

**산출물**

- `scripts/pi/systemd/naver-monitor-poll.service`
- `scripts/pi/systemd/naver-monitor-poll.timer`

**설계 초안**

```ini
# naver-monitor-poll.service
[Unit]
Description=네이버 부동산 — 갱신 요청 폴링
After=network-online.target time-sync.target
Wants=network-online.target time-sync.target

[Service]
Type=oneshot
User=pi
WorkingDirectory=/home/pi/real-estate-monitor
Environment=PYTHONIOENCODING=utf-8
ExecStart=/home/pi/real-estate-monitor/scripts/pi/poll.sh
TimeoutStartSec=25min      # 수집이 멈추면 systemd 가 종료시킨다
```

```ini
# naver-monitor-poll.timer
[Unit]
Description=네이버 부동산 — 2분마다 갱신 요청 확인

[Timer]
OnBootSec=2min
OnUnitActiveSec=2min
AccuracySec=30s

[Install]
WantedBy=timers.target
```

**주의**

- `TimeoutStartSec` 이 폴링 주기보다 길어도 됩니다. `Type=oneshot` 유닛은 systemd 가
  중복 실행하지 않고, `flock` 이 이중 안전장치입니다.
- `poll.sh` 는 잠금을 잡은 채 `exec` 로 `run.sh` 에 넘어갑니다. 유닛에서 보는
  프로세스는 하나뿐이므로 `Type=oneshot` 으로 충분합니다.

**수용 기준**

- [x] `systemd-analyze verify` 통과 — Windows 라 실행 불가, 리뷰로 갈음.
      **Pi 설치 시 `systemd-analyze verify` 로 한 번 확인할 것**
- [x] 타이머가 부팅 후에도 자동 시작 (`WantedBy=timers.target`)
- [x] 비밀값을 요구하는 지시어가 없다 (`EnvironmentFile` 없음)

---

## T4 — 요청 발행 워크플로 `.github/workflows/refresh-request.yml` ✅ 완료

> **구현 결과 — 초안에서 바뀐 점**
> - 초안은 "`permissions:` 를 주지 말라"였지만 **`permissions: {}` 를 명시**했습니다.
>   키를 생략하면 최소 권한이 아니라 **저장소 기본값(쓰기일 수 있음)을 물려받습니다.**
>   빈 맵이라야 토큰에 아무 권한도 주지 않는다는 뜻이 됩니다.
> - `${{ github.event_name }}` 를 `run:` 본문에 직접 넣지 않고 `env:` 를 거칩니다.
>   이 값은 안전하지만, 워크플로에서 표현식을 셸에 직접 보간하는 습관을 남기지
>   않으려는 것입니다.
> - `timeout-minutes: 5` — 아무 일도 안 하는 job 이 러너 이슈로 6시간 매달리지 않게.

**목적.** 갱신 요청을 발행합니다. **아무 일도 하지 않는 것이 정상입니다** —
워크플로 실행 기록 자체가 Pi 에게 보내는 신호입니다.

**산출물.** `.github/workflows/refresh-request.yml`

**설계 초안**

```yaml
name: 갱신 요청

# 이 워크플로는 아무 일도 하지 않는다. **실행됐다는 사실 자체가 신호**다.
# 집 라즈베리파이가 2분마다 Actions API 로 이 워크플로의 최신 실행 id 를 확인하고,
# 지난번에 처리한 id 와 다르면 수집을 시작한다 (DESIGN-PI.md §2).
#
# 수집을 여기서 하지 않는 이유: 네이버가 공용 러너 IP 를 차단한다(실측).
on:
  schedule:
    # 정각은 전 세계 워크플로가 몰려 지연이 커지므로 어중간한 분을 쓴다.
    # (Actions cron 은 5~30분 지연될 수 있다)
    - cron: "30 23 * * *"   # KST 08:30
    - cron: "30 3 * * *"    # KST 12:30
  workflow_dispatch:        # 대시보드 "지금 갱신" 버튼이 여기로 온다

jobs:
  request:
    runs-on: ubuntu-latest
    steps:
      - name: 요청 발행
        run: |
          {
            echo "### 갱신 요청이 발행됐습니다"
            echo ""
            echo "- 트리거: \`${{ github.event_name }}\`"
            echo "- 라즈베리파이가 최대 2분 안에 감지해 수집을 시작합니다."
            echo "- 결과는 커밋과 텔레그램으로 도착합니다 (전체 최대 약 7분)."
          } >> "$GITHUB_STEP_SUMMARY"
```

**주의**

- `permissions:` 를 주지 마세요. 아무것도 쓰지 않습니다(최소 권한).
- 워크플로 **파일명이 Pi 폴러(T2)와 대시보드 버튼(T6)의 계약**입니다. 바꾸려면 셋을
  같이 바꿔야 합니다.
- 이 워크플로가 매일 도는 덕분에 60일 무활동 자동 비활성화도 회피됩니다.

**수용 기준**

- [x] 워크플로 파일명이 `refresh-request.yml` 이다 (T2 의 `WORKFLOW` 와 일치.
      T6 은 아직 `daily.yml` 을 가리킨다)
- [x] `workflow_dispatch` 가 있어 Actions 화면에 `Run workflow` 버튼이 뜬다
- [x] cron 이 UTC 기준으로 KST 08:30 / 12:30 에 해당한다
- [x] 네이버·텔레그램을 호출하지 않는다 (checkout 조차 없다)

---

## T5 — 감시·폴백 워크플로 `.github/workflows/watchdog.yml` (+ `daily.yml` 제거) ✅ 완료

> **구현 결과 — 요구사항에서 정한 것들**
> - `data/state.json` 이 **아예 없어도 "오늘 수집 안 됨"** 으로 봅니다. 설치 직후이거나
>   파일이 날아간 상황인데, 둘 다 조용히 넘어가면 안 되는 일입니다.
> - 경보를 폴백보다 **먼저** 보냅니다. 순서를 뒤집으면 폴백이 예외로 죽었을 때
>   경보 스텝까지 건너뛰어, Pi 가 죽은 날 아무 연락도 안 갑니다.
> - 폴백에도 `--skip-if-done` 을 붙입니다. Pi 가 일부 단지만 받고 죽었을 수 있고,
>   그 경우 남은 단지만 부르면 러너가 네이버를 두드리는 횟수가 줄어듭니다.
> - 무거운 스텝(`setup-python`·`pip install`)까지 전부 `stale == 'true'` 조건입니다.
>   평상시(=대부분의 날)에는 checkout + jq 한 번으로 끝납니다.

**목적.** Pi 가 죽었는지 집 밖에서 감지하고, 그런 날엔 공용 러너로 한 번 대신 수집합니다.

**산출물.** `.github/workflows/watchdog.yml` 신규, `.github/workflows/daily.yml` 삭제

**요구사항**

1. KST 14:00 (`cron: "0 5 * * *"`) + `workflow_dispatch`
2. `data/state.json` 의 `last_success` 를 KST 오늘과 비교
3. 다르면:
   - 텔레그램 경보 (**폴백 성공 여부와 무관하게 보낼 것** — Pi 가 죽은 사실 자체를
     알아야 합니다)
   - 이어서 폴백 수집 `python -m src.main --skip-if-done`
   - 종료코드 2 는 실패로 만들지 말 것 (`::warning::` 으로만)
   - 변경이 있으면 `data/ docs/` 커밋·푸시 (`permissions: contents: write`)
4. 같으면 아무것도 하지 않고 종료

**재사용.** 현행 `daily.yml` 의 secret 확인 · 실행 · 커밋 · 예외 알림 스텝을 거의
그대로 옮길 수 있습니다. `daily.yml:76-85` 의 종료코드 2 처리와 `daily.yml:89-100`
의 커밋 스텝을 참고하세요.

**주의**

- **폴백은 `--outbox` 를 쓰지 마세요.** 공용 러너에는 secret 이 있으므로 그 자리에서
  직접 보냅니다. `--outbox` 를 붙이면 `data/outbox.json` 이 바뀌어 `notify.yml` 까지
  덩달아 돌고, 경보와 리포트가 뒤섞입니다.
- 폴백이 성공하면 `state.json` 에 오늘이 찍혀, Pi 복구 후 `--skip-if-done` 이
  중복 수집·중복 알림을 막습니다. 이 동작이 깨지지 않게 하세요.
- `daily.yml` 을 지울 때 대시보드 버튼이 그 파일을 가리키고 있습니다 → **T6 와 함께
  머지**해야 버튼이 404 로 깨지지 않습니다.

**수용 기준**

- [x] `last_success` 가 오늘이면 텔레그램을 보내지 않고 커밋도 하지 않는다
- [x] 아니면 경보를 보내고 폴백을 시도한다
- [x] 폴백이 429 로 실패해도 job 이 빨간 X 가 되지 않는다
- [x] `--outbox` 를 쓰지 않는다 (`data/outbox.json` 을 건드리지 않는다)
- [x] `daily.yml` 이 삭제됐다. 남은 문서 참조는 T7(`main.py`)·T9(`README.md`·
      `DESIGN.md`)에서 정리한다 — 그 둘까지 끝나야 `grep -rn "daily.yml"` 이 비고,
      `DESIGN-PI.md` 의 "현행은 이랬다" 서술만 남는다

---

## T6 — 대시보드 버튼 연결 변경 `src/htmlgen.py`

**목적.** "지금 갱신" 버튼을 새 요청 워크플로로 보냅니다.

**현재 위치.** `src/htmlgen.py:482`

```python
url = f"https://github.com/{repo}/actions/workflows/daily.yml"
```

**요구사항**

1. `daily.yml` → `refresh-request.yml`
2. 주변 주석(`htmlgen.py:472-474`)이 "Actions 를 돌려 docs/index.html 을 다시 커밋"
   이라고 설명하는데, 이제 **Actions 는 요청만 발행하고 수집은 Pi 가** 합니다.
   주석을 새 구조에 맞게 고치세요.
3. 버튼 옆 또는 툴팁에 소요 시간 안내를 넣으세요 — 최대 약 7분 (폴링 2분 + 수집 3분
   + Pages 반영 2분). 사용자가 누르고 아무 일도 안 일어난다고 느끼지 않게 하는 것이
   목적입니다.

**주의**

- HTML 은 **외부 CDN·폰트·스크립트를 참조하지 않는 단일 자족 파일**이어야 합니다
  (DESIGN.md §2.0.2). 이 원칙을 깨지 마세요.
- `repo` 가 비어 있으면 버튼을 숨기는 기존 동작을 유지하세요.

**수용 기준**

- [ ] 생성된 `docs/index.html` 의 버튼 링크가 `refresh-request.yml` 을 가리킨다
- [ ] `repo=""` 이면 버튼이 나오지 않는다
- [ ] 외부 리소스 참조가 늘지 않았다

---

## T7 — 집 IP 전제로 문구·정책 수정 `src/naver.py`, `src/main.py`

**목적.** 재시도·안내 문구가 "러너 IP 가 매번 바뀐다"는 옛 전제로 쓰여 있습니다.
집 IP 는 안 바뀌므로 **거짓말이 됩니다.** 로직은 대부분 그대로 두고 문구를 고칩니다.

**바꾸지 말 것 (여전히 옳음).** "첫 요청부터 429 면 즉시 포기" 로직
(`naver.py:269-304`). 차단이 40분 이상 지속되므로 같은 IP 로 즉시 재시도하는 건
집에서도 무의미합니다.

**고칠 것**

| 위치 | 현재 | 문제 |
|---|---|---|
| `naver.py:48-62` 주석 | "하루 여러 번 실행해 매번 다른 러너 IP 를 받는다" | 집 IP 는 안 바뀜 |
| `naver.py:299-303` `IPBlocked` 메시지 | "재시도해도 같은 IP 라 풀리지 않습니다" | 취지는 맞으나 후속 안내가 러너 전제 |
| `main.py:9-13` 독스트링 | "예정된 다음 실행이 다른 IP 로 재시도한다" | 같음 |
| `main.py:74-79` 실패 알림 문구 | "오늘 남은 예약 실행이 다른 IP 로 다시 시도합니다" | 같음 |
| `state.py:1-11` 독스트링 | "매번 다른 러너 IP 로 시도" | 같음 |

**새 문구의 방향.** 집 IP 는 하나뿐이므로 대응이 다릅니다.

- 1회 차단 → "12:30 요청이 다시 시도합니다"
- **며칠 연속 차단 → "집 IP 가 실제로 찍혔다는 뜻입니다. 호출 간격을 늘리거나
  며칠 쉬세요"** (러너 시절엔 없던 상황이라 안내가 새로 필요합니다)

**수용 기준**

- [ ] `grep -rn "러너" src/` 결과가 폴백 맥락(watchdog) 외에는 없다
- [ ] `python tests/test_retry.py && python tests/test_state.py` 통과
- [ ] 로직 변경 없음 — diff 가 주석·문자열에 국한 (재시도 상수 포함 동작 불변)

---

## T8 — Pi 설치 가이드 `scripts/pi/README.md` ✅ 완료

> **구현 결과 — 초안에서 추가된 것**
> - **`ssh -T git@github.com` 을 배포 키 단계에 넣었습니다.** 서비스는 비대화식으로
>   돌기 때문에 `known_hosts` 에 항목이 없으면 첫 push 가 확인 프롬프트에서 죽고,
>   push 가 죽으면 그날 알림이 통째로 없습니다. `sudo` 로 하면 root 의 known_hosts
>   에 들어가 소용없다는 점도 적었습니다.
> - **첫 요청 확인 절차를 3단계로 풀어 썼습니다.** 폴러는 최초 1회 마커만 초기화하고
>   수집하지 않으므로, `Run workflow` 를 **두 번** 눌러야 첫 수집이 돕니다. 안 적어
>   두면 "설치했는데 안 돈다"로 오해합니다.
> - venv 를 쓸 때 서비스에 `Environment=PATH=...` 가 필요하다는 주의. `run.sh`·
>   `poll.sh` 가 `python3` 를 이름으로 부르기 때문입니다.
> - `/var/lib/naver-monitor` 수동 생성은 "수동 실행을 먼저 해 볼 때만"으로 축소.
>   평소에는 T3 의 `StateDirectory=` 가 만듭니다.
> - ⚠️ 이 문서는 **T5(watchdog)가 있다는 전제**로 "Pi 가 죽어도 14:00 에 경보"라고
>   적었습니다. T5 머지 전에는 그 안전망이 없습니다.

**목적.** 사람이 Pi 앞에서 그대로 따라 할 수 있는 설치 문서.

**포함할 것**

1. **하드웨어 조건** — 정품급 15W USB-C 어댑터, 방열판, 통풍되는 위치
   (근거는 DESIGN-PI.md §2.7)
2. OS 준비 — Bookworm, `timedatectl set-timezone Asia/Seoul`,
   `apt install git python3-pip jq unattended-upgrades`, journald `SystemMaxUse=50M`
3. **Wi-Fi 절전 해제** — `nmcli connection modify <이름> 802-11-wireless.powersave 2`
   (안 하면 유휴 후 첫 요청이 지연되거나 끊깁니다) + 공유기 DHCP 예약
4. clone → `pip install -r requirements.txt --break-system-packages` (또는 venv)
5. **비밀값 설정 단계는 없습니다** (불변식 5). 텔레그램 확인이 필요하면 Pi 가 아니라
   Actions 에서 하세요 — `notify.yml` 이 실제 전송 경로입니다
6. `/var/lib/naver-monitor/` 를 `pi` 소유로 생성
7. **집 IP 검증** — `python3 -m src.main --dry-run --no-save` 수동 1회.
   (`--dry-run` 이라 저장·전송 없이 네이버 응답만 봅니다. `run.sh` 없이도 됩니다)
   ⚠️ **여기서 429 가 뜨면 멈추고 재검토.** 이 설계 전체의 전제가 "집 IP 는 깨끗하다"
   입니다. 차단되면 `data/state.json` 에 실패 기록이 남으니 `git checkout` 으로
   되돌리세요
8. deploy key 등록 (`ssh-keygen -t ed25519` → GitHub Settings → Deploy keys →
   **Allow write access** 체크) → `git remote set-url origin git@github.com:...`
9. systemd 유닛 설치·활성화 → `journalctl -u naver-monitor-poll -f` 로 관찰
10. 문제 해결 — 로그 보는 법, 수동 실행, 마커 초기화(`/var/lib/naver-monitor/last-request`)

**주의**

- PAT 가 아니라 **deploy key** 를 쓰는 이유를 적으세요: 유출돼도 이 저장소만
  위험하고 만료 관리가 없습니다. **이제 Pi 의 유일한 자격증명입니다** — 잃으면
  push 가 막히고, push 가 막히면 그날 알림도 없습니다 (DESIGN-PI.md §5.4 함정 2).
- 순서가 중요합니다. **7번(집 IP 검증)이 8~9번보다 앞**이어야 합니다. 전제가
  깨지면 나머지 설치가 무의미하기 때문입니다.
- 첫 수집이 성공하면 텔레그램이 **push 직후 1분 안에** 옵니다. Pi 로그에는
  `[대기] 리포트를 …/outbox.json 에 적었습니다` 까지만 찍히는 게 정상이라고
  적어 두세요. 안 그러면 "전송 로그가 없다"고 오해합니다.

**수용 기준**

- [x] 명령을 순서대로 복사해 실행하면 동작하는 상태가 된다 (실기 검증은 Pi 에서)
- [x] 각 단계에 "왜" 가 한 줄씩 붙어 있다
- [x] 비밀값을 Pi 에 넣으라는 단계가 없다
- [x] 텔레그램이 Actions 에서 나간다는 점과 확인 방법(Actions 탭)이 적혀 있다
- [x] 집 IP 검증(§5)이 배포 키(§7)·systemd(§8)보다 앞에 있다

---

## T9 — 기존 문서 갱신 `README.md`, `DESIGN.md`

**목적.** 현행 문서가 "GitHub Actions 에서 하루 7번 수집"을 전제로 쓰여 있어
새 구조와 정면으로 어긋납니다.

**고칠 곳**

| 문서 | 위치 | 내용 |
|---|---|---|
| `README.md` | "매일 자동 실행" 방법 A/B/C | 방법 A 를 **Pi 워커 구조**로 교체. 방법 B(작업 스케줄러)는 수동 대안으로 유지. 방법 C(self-hosted 러너)는 **삭제 권장** — public 저장소 보안 문제가 있고 새 구조가 대체합니다 |
| `README.md` | "429가 뜰 때" | "하루 7번 시도" 서술을 "집 IP 1회 + 보충 1회" 로. 며칠 연속 차단 시 대응 추가 |
| `README.md` | "지금 갱신 버튼" | 소요 시간(최대 ~7분)과 새 동작 |
| `DESIGN.md` | §5 실행 환경 표 | GitHub Actions / 작업 스케줄러 2열 → **Pi 워커** 열 추가하고 권장을 옮김 |
| `DESIGN.md` | §5 대응 3단계 | "하루 7번 → self-hosted → 작업 스케줄러" 순서가 낡음. Pi 워커가 1순위 |
| `DESIGN.md` | §2 아키텍처 다이어그램 | 실행 위치와 트리거 경로 반영 |

**주의**

- **DESIGN.md §1 (데이터 소스·페이싱·429 실측)은 건드리지 마세요.** 실행 위치와
  무관한 실측 기록이고, 이 프로젝트에서 가장 값진 부분입니다.
- 링크로 [DESIGN-PI.md](DESIGN-PI.md) 를 참조하되, 핵심 결론은 본문에도 적으세요.

**수용 기준**

- [ ] "하루 7번" 서술이 남아 있지 않다 (`grep -rn "7번"`)
- [ ] `daily.yml` 참조가 남아 있지 않다
- [ ] 새로 온 사람이 README 만 읽고 구조를 이해할 수 있다

---

## T10 — (선택) 버튼 실행의 텔레그램·스냅샷 정책

**배경.** 버튼으로 강제 수집하면 텔레그램 리포트가 한 번 더 갑니다. 대시보드를
보려고 누른 것뿐인데 알림이 오는 게 성가실 수 있습니다.

**먼저 알아야 할 것 — 현행에서 정보는 누락되지 않습니다.** 버튼 실행이 그 시점에
리포트를 보내기 때문입니다. 스냅샷을 덮어써서 다음 날 아침이 "변동 없음"이 되더라도,
그 변화는 이미 버튼 누른 시각에 전송됐습니다.

```
08:30 아침    10건 수집 → 스냅샷 저장          → 아침 리포트
14:00         신규 1건 등장 (11건)
15:00 버튼    11건 수집, diff = 10 vs 11       → 🆕 1건 전송 ✅ → 스냅샷 저장
다음날 08:30  11건, diff = 11 vs 11            → 변동 없음
```

**함정 — 전송만 끄면 정보가 진짜로 사라집니다.**

```
15:00 버튼    diff = 10 vs 11 → 신규 1건 감지  → 전송 안 함 ❌ → 스냅샷은 저장
다음날 08:30  diff = 11 vs 11                  → 변동 없음  ← 여기서도 안 나옴
```

버튼 실행이 "이미 봤다"고 기록해 놓고 알리지는 않은 상태가 됩니다.

**선택지**

| 안 | 플래그 | 텔레그램 | 스냅샷 | 결과 |
|---|---|---|---|---|
| **A. 현행 유지** | 없음 | 보냄 | 저장 | 정보 완전. 알림이 예정 외 시각에 추가로 옴 |
| B. 전송만 끔 | `--dry-run` | 안 보냄 | 저장 | ⚠️ 정보 누락 — **채택 금지** |
| **C. 읽기 전용 갱신** | `--dry-run --no-save` | 안 보냄 | 저장 안 함 | 대시보드만 최신화. 아침 리포트가 그 변화를 정상 보고 |

**C 를 쓰려면 새 코드가 필요 없습니다.** 두 플래그 다 이미 있고,
`docs/index.html` 은 `--no-save` 와 무관하게 항상 쓰입니다.
T2 의 `workflow_dispatch` 분기에 플래그를 추가하기만 하면 됩니다.

전송 주체가 Actions 로 바뀌어도 이 표는 그대로입니다. `--dry-run` 이 `--outbox`
보다 우선하므로 "텔레그램 안 보냄" = "`data/outbox.json` 을 쓰지 않음" 이고,
파일이 안 바뀌면 `notify.yml` 도 돌지 않습니다.

**C 의 부수 효과 (의도된 동작).** `--no-save` 면 `state.json` 의 완료 표시도 찍히지
않습니다. 버튼 실행은 "아침 리포트를 대신한 것"이 아니므로 이게 맞습니다 — 아침 수집이
없었던 날이라면 watchdog 이 정상적으로 경보를 올립니다.

**결정 대기.** A 와 C 중 사용자 확인 필요. **B 는 어떤 경우에도 채택하지 마세요.**

---

## T12 — 텔레그램 전송 워크플로 `.github/workflows/notify.yml` ✅ 완료

**목적.** Pi 가 적어 놓은 `data/outbox.json` 을 저장소 secret 으로 전송합니다.
Pi 에 토큰을 두지 않기 위한 구조입니다 (불변식 5 · DESIGN-PI.md §5.4).

**산출물** (완료)

- `.github/workflows/notify.yml` — `on: push` + `paths: [data/outbox.json]`
- `src/outbox.py` — 메시지를 파일로 기록 (`version` / `kind` / `generated_at` / `chunks`)
- `src/main.py` — `--outbox` 플래그. 우선순위는 `--dry-run` > `--outbox` > 직접 전송
- `tests/test_outbox.py`

**이 작업에서 배운 것 (다른 작업자도 알아야 함)**

- `generated_at` 이 없으면 **'변동 없음' 리포트가 조용히 사라집니다.** 내용이
  어제와 같으면 커밋이 안 생기고 → push 가 없고 → 워크플로가 안 돕니다.
  `tests/test_outbox.py::test_content_changes_even_when_report_is_identical` 가 지킵니다.
- 길이 분할(4096자)은 파이썬 쪽에서 끝냅니다. bash + jq 로 다시 구현하지 마세요.
- `version` 불일치는 **전송하지 않고 job 을 실패**시킵니다. 스키마를 바꿀 때는
  `src/outbox.py` 의 `VERSION` 과 `notify.yml` 의 검사값을 함께 올리세요.

**남은 확인** (실제 저장소에서만 가능)

- [ ] 첫 Pi push 후 `notify.yml` 이 실제로 트리거된다 (`paths` 필터 동작)
- [ ] Actions 로그에 토큰이 노출되지 않는다

---

## T11 — (향후) GPIO 웨이크 장치 대응

**지금 하지 마세요.** 하드웨어가 붙은 뒤에 진행합니다. 설계상 요청 프로토콜은
바뀌지 않고(§공통 컨텍스트 불변식 1), Pi 쪽 두 가지만 달라집니다.

1. 폴링 타이머 → **부팅 시 1회 폴링** (`WantedBy=multi-user.target`)
2. 처리 후 `halt` 단계 추가. 이때 **반드시** 함께 넣을 것:
   - 데드맨 스위치 — 부팅 직후 `systemd-run --on-active=25min /sbin/halt`.
     `halt` 가 스크립트 성공에 의존하면, 크롤러가 멈췄을 때 Pi 가 영영 켜져 있습니다
   - 정비 모드 플래그 — `/boot/firmware/naver-monitor.stay-on` 이 있으면 halt 생략.
     배포 실수로 부팅→즉시종료 루프에 빠지면 SSH 로 못 들어갑니다. `/boot` 파티션이라
     SD 카드를 PC 에 꽂아 만들 수 있습니다
3. 기상 시각은 **Actions cron 지연(5~30분)보다 뒤**로. 08:30 요청이면 09:10 기상.
4. 전원은 graceful shutdown **후에** 차단. 먼저 끊으면 SD 카드가 손상됩니다.

# 설계 변경안 — 실행 위치를 GitHub 러너에서 라즈베리파이로

> 상태: **설계 확정, 미구현**. 작업 분해는 [TASKS-PI.md](TASKS-PI.md).
> 기준 문서: [DESIGN.md](DESIGN.md) §1.5, §5 / [README.md](README.md) "429가 뜰 때"

**확정 사항** (2026-08-09)

| 항목 | 결정 | 근거 |
|---|---|---|
| 하드웨어 | **Raspberry Pi 4B** | RTC 웨이크 없음 → 전원 껐다 켜는 구조는 지금은 불가 (§3) |
| 전원 정책 | **상시 가동 (전원 상시 공급 가정)** | 화재·내구도 검토 완료(§2.7), 절전 대안 4개 비교 후 채택(§3.2) |
| 네트워크 | **Wi-Fi** | 부팅·복구 시 연결 대기와 절전모드 해제 필요 (§2.6) |
| **갱신 요청 주체** | **GitHub Actions 매일 트리거 + 대시보드 갱신 버튼** | 스케줄러를 GitHub 한 곳으로 모은다 (§2.3) |
| **텔레그램 전송 주체** | **GitHub Actions (`notify.yml`)** | 24시간 켜진 집 기기에 토큰을 두지 않는다 (§5.4) |
| Actions 폴백 | 유지 | Pi 장애 시 데이터 연속성 (§5.2) |
| 향후 | GPIO 웨이크 장치 추가 | 프로토콜을 바꾸지 않도록 지금 설계에 반영 (§6) |

---

## 0. 요약

| | 현행 | 변경안 |
|---|---|---|
| 실행 위치 | GitHub 공용 러너 (IP 매번 바뀜, 자주 차단) | 집 라즈베리파이 4B (고정 집 IP) |
| 스케줄러 | GitHub Actions cron 7회 | **GitHub Actions 요청 워크플로 2회 + 버튼** |
| Pi 의 역할 | — | 요청을 폴링해 **수집**하는 워커 |
| GitHub 역할 | 실행 + 저장 + 게시 | **요청 발행 + 저장 + 게시 + 전송 + 감시 + 폴백** |
| 텔레그램 전송 | Actions (secret) | **Actions (secret) — 그대로** |
| 비밀값 위치 | 저장소 Secret | **저장소 Secret 뿐. Pi 에는 없다** |
| 하루 실행 | 7회 시도 (대부분 차단 확인용) | 1회 수집 (+실패 시 1회 보충) |

---

## 1. 원안(Actions → WOL → Pi)이 성립하지 않는 지점

### 1.1 라즈베리파이는 Wake-on-LAN 을 받지 못한다 ❌

Pi 3 / 4 / 5 **모두** 미지원입니다. 설정 문제가 아니라 보드 설계입니다.

- 종료 시 이더넷 PHY 의 전원이 유지되지 않고 리셋된다
- PHY 의 WOL 핀이 SoC 로 배선돼 있지 않다
- 부트 펌웨어에 그 웨이크 신호를 받아 부팅하는 경로가 없다

Wi-Fi 를 쓰기로 했으므로 더 확실히 불가능합니다(WoWLAN 미지원).

> 흔한 오해: 라즈베리파이는 **WOL 을 보내는 쪽**(항상 켜진 WOL 서버)으로 자주
> 쓰입니다. 검색 결과 대부분이 그 사례라 지원하는 것처럼 보입니다.

### 1.2 GitHub Actions 는 우리집 LAN 에 매직패킷을 넣을 수 없다 ❌

| 방법 | 왜 안 되나 |
|---|---|
| UDP 9번을 서브넷 브로드캐스트로 포워딩 | 가정용 공유기(SKB 포함)는 브로드캐스트 대상 포워딩을 허용하지 않음 |
| 정적 ARP + 미사용 IP 로 포워딩 | 공유기 CLI/정적 ARP 설정 필요 — SKB 임대 공유기는 노출 안 됨 |
| SKB WOL Manager 를 Actions 가 호출 | 공개 API 가 아니라 포털 **로그인 세션** 기반. 통신사 계정을 public 저장소 Secret 에 넣어야 하고 포털 개편마다 깨짐 |

### 1.3 그래서 방향을 뒤집는다 — push 가 아니라 pull

깨우는 신호를 밖에서 안으로 밀어 넣는 대신, **GitHub 이 요청을 걸어두고 Pi 가
가져갑니다.** 인바운드 경로가 아예 필요 없어집니다.

```
        [ 밖 → 안 ]  불가                    [ 안 → 밖 ]  가능
   GitHub ──✗──> 공유기 ──✗──> Pi        Pi ──✓──> GitHub / 네이버 / 텔레그램
   (§1.2 포워딩)   (§1.1 WOL)              (아웃바운드는 아무 설정도 필요 없다)
```

---

## 2. 파이프라인 — GitHub 이 요청하고 Pi 가 처리한다

```
 ┌── 생산자 (요청을 만든다) ────────────────────────────────┐
 │                                                          │
 │  ⏰ Actions 예약   KST 08:30 / 12:30   (event=schedule)  │
 │  🔄 대시보드 버튼  → Run workflow      (event=workflow_dispatch)
 │                    │                                     │
 │                    └─→ .github/workflows/refresh-request.yml
 │                        (아무 일도 하지 않는다. 실행 기록 자체가 신호)
 └──────────────────────────────┬───────────────────────────┘
                                │  GitHub Actions API (인증 불필요·public)
 ┌── 소비자 (Pi, 상시 가동) ─────▼───────────────────────────┐
 │  naver-monitor-poll.timer  (2분마다)                     │
 │      └─ poll.sh                                          │
 │           1. flock — 수집 중이면 물러난다 (요청 보존)      │
 │           2. 최신 run 조회 → id 가 마커와 같으면 종료      │
 │           3. 마커 전진 (at-most-once)                     │
 │           4. event 로 플래그 결정                         │
 │                 schedule         → --skip-if-done         │
 │                 workflow_dispatch→ (플래그 없음, 강제 수집)│
 │           5. run.sh 실행                                  │
 │                └─ 인터넷 대기 → reset --hard origin/main   │
 │                   → python -m src.main --outbox           │
 │                     (리포트를 data/outbox.json 에 적는다)  │
 │                   → commit → push                         │
 └──────────────────────────────┬───────────────────────────┘
                                ▼
 GitHub  ── docs/ → Pages (Pi 가 죽어도 대시보드는 계속 보인다)
    ├─ notify.yml (data/outbox.json 이 바뀐 push) ── secret 으로 텔레그램 전송
    └─ watchdog.yml (KST 14:00) ── state.json 이 오늘이 아니면
                                    텔레그램 경보 + 공용 러너로 폴백 수집
```

Pi 는 **네이버와 GitHub 만** 부른다. 텔레그램은 부르지 않고 부를 수도 없다 —
토큰이 없기 때문이다 (§5.4).

### 2.1 역할

| 주체 | 하는 일 | 안 하는 일 |
|---|---|---|
| Actions `refresh-request.yml` | 요청 발행(예약 2회 + 버튼) | 수집·커밋 |
| Raspberry Pi | 요청 폴링 · 수집 · 리포트 작성 · HTML 생성 · push | **텔레그램 전송**, 인바운드 서비스 (포트 개방 없음) |
| Actions `notify.yml` | `data/outbox.json` 을 secret 으로 전송 | 수집·커밋 |
| Actions `watchdog.yml` | 미수집 감지 · 경보 · 폴백 수집 | 정기 수집 |
| GitHub Pages | 대시보드 게시 | — |

**self-hosted 러너를 쓰지 않는 이유**: 이 저장소는 public 이라 외부 PR 코드가 집 Pi 에서
실행될 수 있습니다(README 방법 C 의 경고). pull 모델은 Pi 가 자기 저장소의 main 만
받아 실행하므로 그 경로가 없습니다.

### 2.2 요청 신호를 "커밋"이 아니라 "워크플로 실행"으로 둔 이유

요청 파일(`data/refresh-request.json`)을 커밋하는 방식도 가능하지만 채택하지 않았습니다.

| | 파일 커밋 방식 | **워크플로 실행 방식** (채택) |
|---|---|---|
| 저장소 기록자 | GitHub + Pi 둘 | **Pi 하나** — push 경쟁이 없다 |
| 커밋 노이즈 | 요청 1건당 2커밋(생성·삭제) | 없음 |
| 요청 확인 | `git fetch` | Actions API `GET .../runs?per_page=1` |
| 인증 | 기존 deploy key | **불필요** (public 저장소) |

평상시 저장소에 쓰는 주체가 Pi 하나뿐이라는 게 큽니다. 폴백이 도는 날만 예외인데,
Pi 가 매 실행 `reset --hard origin/main` 으로 받아가므로 자가 치유됩니다.

### 2.3 요청은 이벤트가 아니라 **상태**다 (중요)

Pi 는 "요청 이벤트를 수신"하지 않고 **"마지막으로 처리한 run id"와 현재 최신 run id 를
비교**합니다. 이 차이가 두 가지를 보장합니다.

- Pi 가 꺼져 있거나 네트워크가 끊긴 동안 발생한 요청도 **복귀 즉시 보입니다.** 놓치지 않습니다.
- **§6 의 GPIO 웨이크 장치를 나중에 붙여도 프로토콜이 바뀌지 않습니다.** 폴링 시점만 달라집니다.

마커 전진은 **수집 전에** 합니다(at-most-once). 수집 실패 시 그 요청은 소실되지만,
그게 2분마다 실패를 재시도하며 네이버를 두드리는 것보다 낫습니다. 놓친 건
12:30 요청이나 watchdog 폴백이 덮습니다.

### 2.4 지연 예산

| 경로 | 지연 | 비고 |
|---|---|---|
| 예약 트리거 | cron 지연 5~30분 + 폴링 ≤2분 + 수집 ~3분 + Pages 1~2분 | 08:30 요청 → 최대 09:10 도착 |
| 버튼 | 폴링 ≤2분 + 수집 ~3분 + Pages 1~2분 | **최대 약 7분** |

분 단위 정시성이 필요하면 Pi 로컬 타이머를 병행해야 하지만, 스케줄러를 GitHub 한
곳으로 모으는 이점을 깨므로 채택하지 않았습니다.

### 2.5 GitHub API 폴링 비용

- public 저장소는 **인증 없이** 워크플로 실행 목록을 읽을 수 있습니다.
- 미인증 한도는 IP 당 시간 60회. **2분 폴링 = 시간 30회**로 여유가 있습니다.
- `ETag` 조건부 요청을 쓰면 변화 없을 때 304 가 오고 한도에서 차감되지 않습니다.
- ⚠️ 저장소를 private 으로 바꾸면 읽기 전용 PAT 가 필요해집니다.

### 2.6 Wi-Fi 이기 때문에 필요한 것

| 항목 | 조치 | 이유 |
|---|---|---|
| 절전 모드 해제 | `sudo nmcli connection modify <이름> 802-11-wireless.powersave 2` | Pi 의 Wi-Fi 절전은 유휴 후 첫 요청 지연·간헐 끊김의 주범 |
| 인터넷 도달 대기 | `run.sh` 첫 단계 (최대 3분) | 부팅·재연결 후 링크가 떠도 DNS 가 늦게 준비됨 |
| 고정 IP | 공유기에서 DHCP 예약 | SSH 정비 편의 |

아웃바운드만 쓰므로 **포트 개방·DDNS 는 전혀 필요 없습니다.**

### 2.7 상시 가동의 안전성·내구도 (검토 완료)

**화재 위험은 실질적으로 없습니다.** Pi 4B 유휴 소비는 **2.85W** — 폰 충전 중인
충전기보다 적습니다. 유휴 온도 45~50°C, 스로틀링 기준 80°C 라 24시간 켜둬도
근처에 가지 않습니다. 위험이 있다면 Pi 가 아니라 전원 어댑터 쪽입니다.

| 항목 | 조치 | 이유 |
|---|---|---|
| 전원 어댑터 | 정품 15W USB-C 또는 신뢰 브랜드 | 남는 폰 충전기는 전압 강하로 재부팅·SD 손상. 화재보다 이쪽이 훨씬 흔한 실제 피해 |
| 방열 | 방열판 + 통풍되는 위치 | 밀폐 케이스를 카펫·침구·책 위에 두지 않는다 |
| SD 카드 수명 | `journald` 쓰기 제한 (`SystemMaxUse=50M`) | **24/7 Pi 가 죽는 원인 1위가 SD 마모·손상** |
| 보안 | `unattended-upgrades` | 24시간 켜진 기기 |

**SD 카드가 죽어도 피해가 작다는 점이 이 설계의 부수 효과입니다.** 데이터가 전부
GitHub 에 있으므로 재설치 + clone 으로 복구됩니다.

> ⚠️ 역설: **껐다 켜는 쪽이 SD 카드에는 더 위험합니다.** SD 손상 원인 1위가
> clean shutdown 없이 전원이 끊기는 것이기 때문입니다 (§3.2 B안).

---

## 3. Pi 4B 에서 "껐다 켜기" 는 지금은 이득이 없다

### 3.1 Pi 4B 는 halt 해도 전기가 안 줄어든다

| | 소비 | 월 전기료 (214원/kWh 가정) |
|---|---|---|
| Pi 4B 헤드리스 상시 가동 | ~2.85W | **약 440원** |
| Pi 4B **종료 상태** | 0.3W ~ 2.9W (구성에 따라) | 0~450원 |
| (참고) Pi 5 RTC 절전, 하루 10분 가동 | ~0.02W | 약 0원 |

Pi 4B 는 종료해도 PMIC 구성에 따라 유휴와 거의 같은 전력을 먹습니다(이더넷 연결 시
2.9W 실측 보고). Pi 5 의 3mA 같은 절전이 **애초에 존재하지 않습니다.**

### 3.2 검토했으나 채택하지 않은 대안

| 방안 | 방법 | 이득 | 대가 |
|---|---|---|---|
| **A. 상시 가동** ✅ | systemd timer 폴링 | — | 월 ~440원 |
| B. 스마트플러그 예약 | 콘센트를 시간대로 차단. Pi 4 는 전원 인가 시 자동 부팅 | 플러그가 0.5~1W 를 먹어 실질 절감 미미 | **작업이 길어지면 전원이 도중에 끊겨 SD 손상.** 안전해지려다 새 고장 원인이 생김 |
| C. Witty Pi 4 전원관리 HAT | RTC + e-latching 스위치. graceful shutdown 후 완전 차단, 예약 기상 | 진짜 0W | 3~5만원. 월 440원 기준 회수에 7년 |
| D. GPIO3 단락 | halt 에서 깨움 | Pi 4 halt 가 애초에 안 낮음(§3.1) | 깨워줄 장치가 또 필요 → §6 |

---

## 4. "지금 갱신" 버튼

정적 페이지에서 브라우저가 네이버를 직접 부르는 것은 불가능합니다(DESIGN.md §2.0.2).
버튼은 지금처럼 Actions 화면으로 보내되, **대상 워크플로만 바뀝니다.**

- 현행: `htmlgen.py:482` 가 `.../actions/workflows/daily.yml` 로 링크
- 변경: `.../actions/workflows/refresh-request.yml`

거기서 `Run workflow` 를 누르면 요청이 발행되고, Pi 가 2분 안에 집어가 강제 수집합니다
(`--skip-if-done` 없이). 페이지에 토큰이 박히지 않고 인바운드 포트도 없습니다.

---

## 5. GitHub Actions 의 새 역할

### 5.1 요청 발행 `refresh-request.yml`

- `schedule` 2회 (KST 08:30 / 12:30) + `workflow_dispatch`
- **job 은 아무 일도 하지 않습니다.** 실행 기록 자체가 신호입니다.
- 매일 도므로 60일 무활동 자동 비활성화도 함께 회피됩니다.

### 5.2 감시 · 폴백 `watchdog.yml` (KST 14:00)

1. `data/state.json` 의 `last_success` 가 오늘이 아니면 → 텔레그램 경보
2. 이어서 공용 러너로 폴백 수집 1회 → 성공 시 커밋·푸시

- 러너 IP 차단 확률은 예전과 같습니다. **되면 좋고 안 되면 그만인 보조 경로**입니다.
- 폴백이 성공하면 `state.json` 에 오늘이 찍히므로, Pi 복구 후 `--skip-if-done` 덕분에
  중복 수집·중복 알림이 없습니다.
- 경보는 폴백 성공 여부와 **무관하게** 보냅니다. Pi 가 죽은 사실 자체를 알아야 합니다.

### 5.3 없애는 것

- `daily.yml` 전체 → `refresh-request.yml` + `watchdog.yml` 로 분리 대체

### 5.4 텔레그램 전송 `notify.yml` — 만드는 쪽과 보내는 쪽을 나눈다

**결정: 토큰은 저장소 Secret 에만 둔다. Pi 에는 비밀값을 하나도 두지 않는다.**
24시간 켜져 있고 물리적으로 집에 놓인 기기라 유출 경로가 하나 더 생기는 것을
피했습니다. 부수 효과로 `/etc/naver-monitor.env`, systemd `EnvironmentFile`,
설치 가이드의 권한 설정 단계가 통째로 사라집니다.

그래서 Pi 는 **완성된 메시지를 저장소에 적고**, 그 push 를 감지한 Actions 가 보냅니다.

```
Pi:  python -m src.main --outbox   →  data/outbox.json  →  git push
                                                              │ push (paths: data/outbox.json)
GitHub:                                      notify.yml ──────┘ → Bot API
```

`data/outbox.json` 형식 (`src/outbox.py`):

```json
{ "version": 1, "kind": "report", "generated_at": "2026-08-09 08:41:03 KST",
  "chunks": ["...", "..."] }
```

- 전송 쪽이 bash + jq 라 길이 분할(4096자)을 다시 구현하지 않도록 **미리 쪼개서** 넘깁니다.
- `version` 이 맞지 않으면 보내지 않고 job 을 실패시킵니다. 스키마가 어긋난 채
  엉뚱한 메시지가 나가는 것보다 알림이 끊기고 빨간 X 가 뜨는 편이 낫습니다.
- 실패 알림(`kind: "failure"`)도 같은 경로를 탑니다. Pi 에는 토큰이 없으니 예외가 없습니다.

**⚠️ 함정 1 — `generated_at` 이 없으면 '변동 없음' 리포트가 사라집니다.**
내용이 어제와 똑같으면 git 이 변경을 보지 못해 커밋도 push 도 생기지 않고,
push 가 없으면 워크플로가 돌지 않습니다. 아무도 오류를 못 보는데 알림만 안 옵니다.
생성 시각을 매번 싣는 이유가 이것뿐입니다.

**⚠️ 함정 2 — push 가 알림의 유일한 경로가 됐습니다.**
예전에는 Pi 가 직접 보내서 push 가 실패해도 알림은 갔습니다. 이제는 push 실패 =
그날 알림 없음입니다. `run.sh` 의 rebase 재시도와 `watchdog.yml` 이 그 대비입니다.

**⚠️ 함정 3 — Pi 는 반드시 deploy key(SSH)로 push 해야 합니다.**
Actions 의 `GITHUB_TOKEN` 으로 만든 push 는 다른 워크플로를 트리거하지 않습니다
(GitHub 의 무한루프 방지). Pi 인증을 그쪽으로 바꾸면 **커밋은 멀쩡히 올라오는데
알림만 조용히 끊깁니다** — 증상이 "아무 일도 안 일어남"이라 원인을 찾기 어렵습니다.
뒤집어 말하면 이 규칙 덕분에 폴백 커밋이 실수로 `notify.yml` 을 깨우는 일도 없습니다.

**폴백은 예외입니다.** `watchdog.yml` 은 공용 러너에서 돌고 secret 을 직접 쓰므로
`--outbox` 없이 그 자리에서 보냅니다. outbox 를 건드리지 않으니 `notify.yml` 이
덩달아 도는 일도 없습니다.

**대안으로 검토했으나 채택하지 않은 것**: Actions 가 `HEAD~1` 과 `HEAD` 의
`snapshot.json` 을 비교해 리포트를 다시 만드는 방식. 저장소에 리포트 텍스트가
남지 않는 장점이 있지만, 한 push 에 커밋이 여러 개거나 폴백 커밋이 끼면 비교
기준이 흔들려 오보가 납니다. 리포트 본문은 대시보드(`docs/index.html`)로 이미
공개돼 있어 저장소에 남는 것이 새로운 노출도 아닙니다.

---

## 6. 향후 — GPIO 웨이크 장치를 붙일 때

§2.3 덕분에 **요청 프로토콜은 그대로 둡니다.** 바뀌는 것은 Pi 쪽 두 가지뿐입니다.

1. 폴링 타이머 → 부팅 시 1회 폴링 (`WantedBy=multi-user.target`)
2. 처리 후 `halt` 단계 추가 (+ 정비 모드 플래그, 데드맨 스위치 복원)

설계 시 유의할 점:

| 항목 | 내용 |
|---|---|
| 기상 시각 | Actions cron 은 5~30분 지연됩니다. **08:30 요청이면 09:10 기상**이 안전 |
| 웨이크 장치 | GitHub 을 볼 필요 없음. 고정 시각 기상만 하면 됨 (요청 유무는 Pi 가 판단) |
| 놓친 요청 | 꺼져 있는 동안의 요청도 기상 후 마커 비교로 보입니다 (§2.3) |
| 즉시 갱신 | 꺼져 있는 동안 누른 버튼은 다음 기상 때 처리됩니다 — 절전의 대가 |
| SD 카드 | 반드시 graceful shutdown 후 차단. 전원을 먼저 끊으면 안 됨 (§2.7) |

---

## 7. 코드에 미치는 영향

| 파일 | 변경 |
|---|---|
| `src/naver.py` | 로직 그대로. **문구만 수정** — "다음 예약 실행이 다른 IP 로 재시도" 는 집 IP 에선 거짓 |
| `src/main.py` | `--outbox` 추가 (전송 대신 파일로). 종료코드 2 의 의미가 "IP 오염 → 다른 러너 기대" 에서 "집 IP 일시 차단 → 다음 요청에 재시도" 로 바뀜 |
| `src/outbox.py` | **신규** — 보낼 메시지를 `data/outbox.json` 에 기록 (§5.4) |
| `src/htmlgen.py` | 버튼 링크 `daily.yml` → `refresh-request.yml` (§4) |
| `src/telegram.py` | 변경 없음. Actions(폴백·`--test-telegram`)에서 계속 쓰인다 |
| `src/state.py`, `diff.py`, `filters.py`, `report.py` | 변경 없음 |
| `config.yaml` | 변경 없음 (`site.repo` 를 Pi 폴러도 재사용) |
| `scripts/pi/` | 신규 — `run.sh`, `poll.sh`, systemd 유닛, 설치 가이드 |
| `.github/workflows/` | `daily.yml` 제거, `refresh-request.yml`·`watchdog.yml`·`notify.yml` 신규 |
| `DESIGN.md` §5, `README.md` 방법 A/B/C | 갱신 필요 |

### 7.1 재시도 정책은 다시 판단해야 한다

현행 "첫 요청부터 429 면 **즉시 포기**, 다음 실행이 다른 IP 로 받는다" 는
**러너 IP 가 매번 바뀐다는 전제**에서 나왔습니다. 집 IP 는 안 바뀝니다.

- ✅ 유지: 차단이 40분+ 지속되므로 같은 IP 로 즉시 재시도하는 건 여전히 무의미
- 🔧 변경: 요청 트리거 7회 → **2회**. 집 IP 를 우리 손으로 오염시킬 이유가 없음
- 🔧 변경: 차단이 **며칠 연속**이면 집 IP 가 실제로 찍혔다는 뜻이므로 알림 문구가
  달라야 합니다 ("다음 실행을 기다리세요" ❌ → "호출 간격을 늘리거나 며칠 쉬세요")

---

## 8. 실패 모드

| 상황 | 증상 | 대응 |
|---|---|---|
| Pi 다운 · Wi-Fi 끊김 | 마커가 전진하지 않음 | 복구 즉시 대기 중이던 요청 처리 (§2.3) + watchdog 경보·폴백 |
| 요청은 소비했으나 수집 실패 | 마커만 전진, 데이터 없음 | 12:30 요청 또는 watchdog 폴백이 커버 (at-most-once 의 대가) |
| 네이버 차단 | 종료코드 2 | 12:30 요청이 재시도. 이틀 연속이면 §7.1 문구 재검토 |
| 수집이 폴링 주기보다 오래 걸림 | 중복 실행 위험 | `flock` — 마커를 건드리지 않고 물러나므로 요청이 보존됨 |
| 스크립트가 멈춤 | 서비스가 안 끝남 | `TimeoutStartSec=25min` 으로 systemd 가 종료 |
| push 실패 (키 만료·충돌) | 로컬에만 데이터, **텔레그램도 안 옴** | rebase 후 1회 재시도 → 실패 시 다음 실행이 `reset --hard` 로 버림. **그날 diff 1회 누락 + 알림 누락** ⚠️ (§5.4 함정 2) |
| GitHub API 한도 초과 | 폴링이 조용히 실패 | `curl -f` 실패 시 마커 유지 → 다음 폴링에서 재시도 |
| 전송 secret 만료·오류 | `notify.yml` 이 빨간 X | 데이터는 이미 저장소에 있다. secret 교체 후 워크플로 재실행이면 그대로 전송된다 |
| outbox 스키마 불일치 | `notify.yml` 이 빨간 X, 알림 없음 | `src/outbox.py` 의 `VERSION` 과 `notify.yml` 의 검사값을 함께 올린다 |

---

## 참고

- [Does the Pi 4 support wake on lan (wol)? — Raspberry Pi Forums](https://forums.raspberrypi.com/viewtopic.php?t=244669)
- [How much power does the Pi4B use? — RasPi.TV](https://raspi.tv/2019/how-much-power-does-the-pi4b-use-power-measurements)
- [RPi 4 Model B Power Consumption in Standby/Shutdown State — Raspberry Pi Forums](https://forums.raspberrypi.com/viewtopic.php?t=278779)
- [Raspberry Pi 5 RTC — Auto wake up and shutdown — Raspberry Pi Forums](https://forums.raspberrypi.com/viewtopic.php?t=364576)
- [Witty Pi 4 — UUGear](https://www.uugear.com/product/witty-pi-4/)

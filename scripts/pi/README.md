# 라즈베리파이 설치 가이드

Pi 앞에 앉아 **위에서 아래로 그대로 따라 하면** 되도록 썼습니다.
각 단계에 "왜" 를 한 줄씩 붙였습니다 — 나중에 뭔가 고칠 때 그 줄이 판단 근거입니다.

설계 배경은 [DESIGN-PI.md](../../DESIGN-PI.md) 에 있습니다.

## 이 Pi 가 하는 일

```
GitHub Actions  refresh-request.yml   ← 예약(KST 08:30·12:30) + 대시보드 버튼
        │  실행 기록 자체가 신호 (아무 일도 하지 않는 워크플로)
        │
   [Pi] poll.sh   2분마다 Actions API 로 최신 실행 id 확인
        └─ run.sh  인터넷 대기 → reset --hard origin/main → 수집 → git push
                     └ 리포트는 data/outbox.json 에 **적기만** 한다
        │
GitHub Actions  notify.yml  (outbox.json 이 바뀐 push) → 텔레그램 전송
                watchdog.yml (KST 14:00) → 미수집이면 경보 + 폴백 수집
```

**Pi 에는 텔레그램 토큰을 두지 않습니다.** 24시간 켜 두는 집 기기에 비밀값을 두지
않으려는 것이고, 그래서 아래에 secret 설정 단계가 아예 없습니다. 대신 **push 가
알림의 유일한 경로**입니다 — push 가 막히면 그날 알림도 없습니다.

---

## 1. 하드웨어

| 항목 | 요구 | 왜 |
|---|---|---|
| 전원 | **정품급 15W (5V/3A) USB-C 어댑터** | 전압이 처지면 SD 카드 쓰기 중 손상이 납니다. 상시 가동에서 가장 흔한 고장 원인입니다 |
| 냉각 | 방열판 필수, 팬 있으면 더 좋음 | 24시간 켜 두면 스로틀링 온도(80℃)에 걸립니다 |
| 위치 | 통풍되는 곳, 서랍·책장 안 금지 | 위와 같음 |
| SD 카드 | A1/A2 등급 이상 | 로그·git 쓰기가 계속 발생합니다 (§2 의 journald 제한과 함께) |

근거: [DESIGN-PI.md §2.7](../../DESIGN-PI.md).

---

## 2. OS 준비

Raspberry Pi OS **Bookworm** (64-bit) 기준입니다.

```bash
sudo timedatectl set-timezone Asia/Seoul
```

> 왜: 리포트·상태 파일의 "오늘"이 KST 기준입니다. 시계가 UTC 면 자정 근처에서
> `--skip-if-done` 판단과 커밋 날짜가 하루씩 어긋납니다.

```bash
sudo apt update
sudo apt install -y git python3-pip jq curl unattended-upgrades
```

> 왜: `jq` 는 `poll.sh` 가 Actions API 응답을 파싱하는 데 씁니다(없으면 조용히
> 아무 요청도 감지하지 못합니다). `unattended-upgrades` 는 상시 인터넷에 붙어 있는
> 기기라 보안 패치를 자동으로 받게 하려는 것입니다.

```bash
sudo sed -i 's/^#\?SystemMaxUse=.*/SystemMaxUse=50M/' /etc/systemd/journald.conf
sudo systemctl restart systemd-journald
```

> 왜: 2분마다 유닛이 돌면 로그가 계속 쌓입니다. 상한을 두지 않으면 SD 카드 수명과
> 여유 공간을 갉아먹습니다.

---

## 3. Wi-Fi (유선이면 건너뛰기)

```bash
nmcli -t -f NAME connection show          # 연결 이름 확인
sudo nmcli connection modify "<연결 이름>" 802-11-wireless.powersave 2
sudo nmcli connection down "<연결 이름>" && sudo nmcli connection up "<연결 이름>"
```

> 왜: Pi 의 Wi-Fi 절전은 **유휴 후 첫 요청 지연·간헐 끊김의 주범**입니다. 2분마다
> 짧게 한 번 호출하는 이 워크로드가 정확히 그 패턴에 걸립니다. `2` = 끄기.

공유기에서 이 Pi 의 MAC 에 **DHCP 예약**을 걸어 두세요.
> 왜: SSH 로 들어갈 때 IP 를 매번 찾지 않기 위해서입니다. 동작에는 영향 없습니다
> (Pi 는 아웃바운드만 씁니다 — **포트 개방·DDNS 는 전혀 필요 없습니다**).

---

## 4. 저장소와 의존성

유닛 파일이 `pi` 사용자와 `/home/pi/real-estate-monitor` 경로를 전제합니다.
다른 이름을 쓴다면 §8 에서 함께 고칩니다.

```bash
cd ~
git clone https://github.com/dudtj5307/real-estate-monitor.git
cd real-estate-monitor
pip install -r requirements.txt --break-system-packages
```

> 왜 HTTPS 로 clone 하나: 아직 배포 키가 없습니다. §8 에서 SSH 로 바꿉니다.
> 읽기는 public 이라 인증이 필요 없습니다.
>
> 왜 `--break-system-packages` 인가: Bookworm 은 시스템 파이썬에 pip 설치를 막습니다.
> 이 플래그는 `pi` 사용자 홈(`~/.local`)에만 설치하며, 유닛도 `pi` 로 돌기 때문에
> 그대로 보입니다. **`sudo pip` 는 쓰지 마세요** — 시스템 패키지와 충돌합니다.
>
> venv 를 쓰고 싶다면: `run.sh` 와 `poll.sh` 가 `python3` 를 이름으로 부르므로
> §8 의 서비스 파일에 `Environment=PATH=/home/pi/real-estate-monitor/.venv/bin:/usr/bin:/bin`
> 을 추가해야 합니다. 안 하면 시스템 파이썬이 잡혀 `yaml` 을 못 찾습니다.

**비밀값 설정 단계는 없습니다.** 텔레그램 토큰은 저장소 Secret 에만 있고 Pi 는
모릅니다. 전송이 되는지 확인하고 싶으면 Pi 가 아니라 GitHub 의 Actions 탭에서
`텔레그램 전송` 워크플로 실행 결과를 보세요.

---

## 5. 🚩 집 IP 검증 — 여기서 멈출 수도 있습니다

**이 설계 전체의 전제가 "집 IP 는 네이버에 깨끗하다" 입니다.** 배포 키를 만들고
서비스를 등록하기 **전에** 그 전제부터 확인합니다. 깨지면 나머지가 무의미합니다.

```bash
cd ~/real-estate-monitor
python3 -m src.main --dry-run --no-save
```

- `--dry-run` 이라 전송하지 않고 콘솔에만 찍습니다.
- `--no-save` 라 스냅샷을 덮어쓰지 않습니다.
- 단지 하나당 25초씩 쉬므로 몇 분 걸립니다. 정상입니다 (페이싱은 실측값이라
  **절대 줄이지 마세요** — DESIGN.md §1).

**매물 목록이 콘솔에 나오면 통과입니다.** §6 으로.

**429 / `IPBlocked` 가 뜨면 멈추세요.** 집 IP 가 이미 찍혀 있다는 뜻입니다.
며칠 두었다 다시 시도하거나, 설정에서 단지 수를 줄이고 재검토하세요.
이때 `data/state.json` 에 실패 알림 기록이 남으므로 되돌립니다:

```bash
git checkout -- data/
```

> 왜 되돌리나: 그 기록이 남은 채로 운영에 들어가면 "오늘은 이미 실패를 알렸다"고
> 판단해 첫날 실패 알림이 나가지 않습니다.

---

## 6. 상태 디렉터리

`poll.sh` 는 마지막으로 처리한 요청 id 를 `/var/lib/naver-monitor/last-request` 에
둡니다. 서비스가 처음 돌 때 systemd 가 `StateDirectory=` 로 만들어 주므로 보통은
할 일이 없습니다. **수동으로 `poll.sh` 를 먼저 돌려 보려면** 미리 만드세요:

```bash
sudo install -d -o pi -g pi /var/lib/naver-monitor
```

> 왜 `/tmp` 가 아닌가: 재부팅해도 살아남아야 합니다. 마커가 사라지면 폴러는
> "최초 설치" 로 판단해 그 요청을 건너뜁니다.

---

## 7. 배포 키 (deploy key)

Pi 가 결과를 push 할 수 있어야 합니다. **이제 Pi 의 유일한 자격증명입니다.**

```bash
ssh-keygen -t ed25519 -C "raspberrypi-naver-monitor" -f ~/.ssh/id_ed25519 -N ""
cat ~/.ssh/id_ed25519.pub
```

출력된 공개키를 GitHub 저장소 → **Settings → Deploy keys → Add deploy key** 에
붙여넣고 **`Allow write access` 를 체크**합니다.

> 왜 PAT 가 아니라 deploy key 인가: 유출돼도 **이 저장소 하나만** 위험하고, 계정
> 전체 권한이나 만료 관리가 없습니다. 대신 잃어버리면 push 가 막히고,
> push 가 막히면 **그날 알림도 통째로 없습니다** (DESIGN-PI.md §5.4).

```bash
ssh -T git@github.com          # 호스트 키를 known_hosts 에 등록 (yes 입력)
git remote set-url origin git@github.com:dudtj5307/real-estate-monitor.git
git push --dry-run origin HEAD:main
```

> 왜 `ssh -T` 를 먼저 하나: 서비스는 비대화식으로 돕니다. `known_hosts` 에 항목이
> 없으면 첫 push 가 확인 프롬프트에서 실패합니다. **반드시 `pi` 사용자로**
> 실행하세요 (`sudo` 로 하면 root 의 `known_hosts` 에 들어가 소용없습니다).
>
> `Hi dudtj5307/real-estate-monitor! You've successfully authenticated...` 가 나오면
> 성공입니다. 셸 접근이 안 된다는 안내는 정상입니다.

---

## 8. systemd 유닛 설치

사용자·경로가 다르면 먼저 고칩니다 (기본값: `pi`, `/home/pi/real-estate-monitor`).

```bash
cd ~/real-estate-monitor
sudo cp scripts/pi/systemd/naver-monitor-poll.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now naver-monitor-poll.timer
systemctl list-timers naver-monitor-poll.timer
```

> 왜 `.timer` 만 enable 하나: `.service` 는 타이머가 부르는 1회성 유닛이라
> `[Install]` 이 없습니다. 직접 enable 하면 부팅 때 한 번만 돌고 끝납니다.

동작 관찰:

```bash
journalctl -u naver-monitor-poll -f
```

---

## 9. 첫 요청으로 확인하기

순서가 있습니다. 폴러는 **최초 1회는 마커만 초기화하고 수집하지 않습니다**
(설치하자마자 요청도 없는데 네이버를 두드리지 않으려는 설계).

1. GitHub → **Actions → 갱신 요청 → Run workflow** 를 한 번 누릅니다.
   - 이 워크플로가 한 번도 돈 적이 없으면 Pi 로그에 `실행 기록이 없다` 만 찍힙니다.
2. 2분 안에 Pi 로그에 `마커 초기화: run=... (첫 수집은 다음 요청부터)` 가 뜹니다.
3. **다시 한 번 Run workflow** 를 누릅니다.
4. 2분 안에 `요청 감지: run=... event=workflow_dispatch → 수집 시작` 이 뜨고
   수집이 시작됩니다.

정상 종료 로그는 이렇게 끝납니다:

```
[대기] 리포트를 .../data/outbox.json 에 적었습니다 — 전송은 Actions 가 합니다
[run] 수집 완료
```

> **Pi 로그에 "텔레그램 전송" 이 없는 게 정상입니다.** 전송은 push 를 감지한
> Actions(`텔레그램 전송` 워크플로)가 하고, 보통 push 후 1분 안에 도착합니다.
> 안 오면 Actions 탭에서 그 워크플로의 실행 결과를 보세요.

---

## 문제 해결

**로그 보기**

```bash
journalctl -u naver-monitor-poll -n 100 --no-pager   # 최근 100줄
journalctl -u naver-monitor-poll --since today
systemctl status naver-monitor-poll.timer
```

**수동으로 한 번 수집** — 요청 없이 강제로 돌립니다.

```bash
cd ~/real-estate-monitor
flock -n /tmp/naver-monitor.lock ./scripts/pi/run.sh
```

> 왜 `flock` 으로 감싸나: `run.sh` 는 잠금을 잡지 않습니다(`poll.sh` 가 잡은 잠금을
> 물려받는 구조). 감싸지 않으면 타이머가 부른 수집과 동시에 돌 수 있습니다.
>
> ⚠️ `run.sh` 는 시작할 때 `git reset --hard origin/main` 을 합니다. Pi 에서 편집한
> 내용은 사라집니다. 코드 수정은 PC 에서 하고 push 하세요.

**요청을 다시 처리하게 하기** (마커 초기화)

```bash
sudo rm /var/lib/naver-monitor/last-request
```

> 다음 폴링이 "최초 설치" 로 보고 마커만 다시 씁니다. 즉시 수집되지는 않으니,
> 그 뒤에 Run workflow 를 한 번 더 누르세요.

**증상별**

| 증상 | 확인할 것 |
|---|---|
| 로그에 `실행 기록이 없다` | `refresh-request.yml` 이 아직 한 번도 실행되지 않음 → Actions 에서 1회 실행 |
| 로그에 `API 응답 403` | 미인증 API 한도 초과(IP 당 시간 60회). 같은 IP 의 다른 기기가 쓰고 있는지 확인 |
| 커밋은 올라오는데 텔레그램이 없음 | Actions → `텔레그램 전송` 워크플로. secret 이 비었거나 `outbox` 버전 불일치 |
| `push 재시도 실패` | 배포 키의 write access 체크 여부, `ssh -T git@github.com` (pi 사용자로) |
| `마커를 쓸 수 없다` | SD 카드가 읽기전용으로 떨어졌을 가능성 → `dmesg \| tail`, 카드 교체 |
| 수집이 429 로 계속 실패 | 집 IP 가 찍혔습니다. §5 로 돌아가 며칠 쉬거나 단지 수를 줄이세요 |
| 아무 로그도 없음 | `systemctl list-timers` 로 타이머가 살아 있는지, `daemon-reload` 를 했는지 |

**Pi 가 죽었을 때** — 집 밖에서도 알 수 있습니다. KST 14:00 에 `watchdog` 이
그날 수집이 없었는지 확인해 텔레그램으로 경보하고, 공용 러너로 한 번 대신
수집합니다. 즉 **Pi 가 며칠 꺼져 있어도 데이터가 완전히 끊기지는 않습니다.**

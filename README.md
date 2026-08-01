# 부동산 모니터

매일 아침 9시, 네이버 부동산에서 지정한 아파트 단지의 매물을 수집해
**신규 / 가격변동 / 소진**을 텔레그램으로 보고합니다.

설계 근거와 API 스펙은 [DESIGN.md](DESIGN.md)를 보세요.

---

## 빠른 시작

```bash
pip install -r requirements.txt
python -m src.main --dry-run --no-save
```

텔레그램 설정 없이도 콘솔에 리포트가 출력됩니다.

### 출력 예시

```
🏠 부동산 리포트 · 2026-07-31 (금)

━━ 성복역현대홈타운 ━━
💰 매매
[20평대] 5건 (▲1) · 10.00억~11.00억
  🆕 204동 7/20층 · 10.30억
     중문 방주방확장등 최근특올수리 화이트톤 로얄동굿라인 입주협의

[30평대] 13건 (▼1) · 10.00억~12.30억
  💸 201동 1/20층 · 10.80억 → 10.50억 (-3,000만)
  ❌ 소진 1건
```

실행하면 텔레그램 리포트와 함께 **`docs/index.html` 대시보드**가 생성됩니다.
브라우저로 바로 열어볼 수 있습니다.

---

## HTML 대시보드

매 실행마다 `docs/index.html`이 갱신됩니다. 외부 CDN·폰트·스크립트를 전혀 쓰지 않는
**단일 파일**이라 그냥 열기만 하면 동작합니다.

- **거래유형 선택** — `매매` / `전세` 탭. 수집은 한 번에 하고 보기만 전환합니다
- **평형 선택** — `전체` / `20평대` / `30평대`. 현재 조건에 없는 평형은 자동 비활성화
- **금액대** — 억 단위 입력칸 두 개(예: `9` ~ `11`). `전체` 버튼으로 해제
- **신규·변동만** 토글
- **단지별 접기/펼치기** — 단지 이름줄을 누르면 접힙니다. 접어도 제목 옆에
  `매매 12건 · 9.2억~11.0억 · 신규 2` 요약이 남고, 접힘 상태는 브라우저에
  기억됩니다. 단지가 둘 이상이면 헤더에 `전체 접기/펼치기` 버튼이 붙습니다
- 건수 / 가격대 / 신규 / 가격변동 / 소진 통계는 **선택에 따라 실시간 재계산**
- 정렬 — 표 머리글 클릭 (가격/평형/층/확인일 등)
- 🆕 신규, 가격변동은 이전 가격과 증감액을 함께 표시
- 다크/라이트 자동 + 우측 상단 토글 (선택은 브라우저에 기억됨)
- 모바일에서 페이지는 가로 스크롤되지 않고 표만 자체 스크롤

### "지금 갱신" 버튼

정적 페이지는 브라우저에서 네이버를 직접 호출할 수 없습니다
(CORS 차단 + 네이버 세션 쿠키가 필요). 그래서 헤더의 `🔄 지금 갱신`은
**Actions 실행 화면으로 연결**되고, 거기서 `Run workflow`를 누르면
2분쯤 뒤 페이지가 새 데이터로 갱신됩니다.

버튼 링크는 `config.yaml`의 `site.repo`로 정합니다. 비워두면 버튼이 사라집니다.
금액대 입력칸의 시작값은 `site.price_focus`로 정합니다 (만원 단위).

더 자주 자동 갱신하고 싶으면 `.github/workflows/daily.yml`의 cron을 늘리세요.
예를 들어 `0 0,6 * * *` 는 KST 09시·15시 두 번 실행합니다.

### GitHub Pages로 공개하기

1. 저장소를 GitHub에 push
2. Settings → **Pages**
3. Source: **Deploy from a branch**, Branch: **`main`** / 폴더 **`/docs`** → Save
4. 1~2분 뒤 `https://<사용자명>.github.io/<저장소명>/` 에서 접속

매일 워크플로가 `docs/`를 커밋하므로 페이지도 자동으로 갱신됩니다.

> 저장소가 **public**이면 이 주소는 누구나 볼 수 있습니다. 비공개로 두려면
> 저장소를 private으로 만들고 (GitHub Pages private 게시는 유료 플랜 필요)
> `docs/index.html`을 로컬에서 직접 열어 보세요.

---

## 설정

`config.yaml`을 수정합니다.

```yaml
defaults:
  trade_types: [매매]        # 매매 / 전세 / 월세 / 단기임대
  pyeong_groups: [20, 30]   # 공급면적 기준 평형대. []면 전체
  price_max: null           # 만원 단위. 예) 100000 = 10억

complexes:
  - name: 성복역현대홈타운
    number: "8692"
  - name: 성복역아이파크
    number: "3707"
```

- **단지 추가**: `complexes` 항목을 늘리면 됩니다. 단지번호는
  `https://fin.land.naver.com/complexes/{번호}` URL의 숫자입니다.
- **단지별 조건**: 각 단지 항목 안에 `trade_types` / `pyeong_groups` /
  `price_max`를 쓰면 `defaults`를 덮어씁니다.

---

## 텔레그램 연결

1. 텔레그램에서 `@BotFather` → `/newbot` → **봇 토큰** 발급
2. 만든 봇에게 아무 메시지나 한 번 전송
3. `https://api.telegram.org/bot<토큰>/getUpdates` 접속 → `chat.id` 확인

### 환경변수 / Secret 이름

| 이름 | 값 | 예시 |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | BotFather가 준 토큰 | `8123456789:AAHk9x_...` |
| `TELEGRAM_CHAT_ID` | `getUpdates`의 `chat.id` | `123456789` |

**이 저장소는 public이므로 두 값 모두 파일에 적지 마세요.**
`config.yaml`의 `chat_id`는 환경변수가 없을 때만 쓰이는 로컬 폴백입니다.

```powershell
# PowerShell (현재 세션)
$env:TELEGRAM_BOT_TOKEN = "..."
$env:TELEGRAM_CHAT_ID   = "..."
python -m src.main
```

```powershell
# 영구 등록 (작업 스케줄러용) — 새 터미널부터 적용
[Environment]::SetEnvironmentVariable("TELEGRAM_BOT_TOKEN","...","User")
[Environment]::SetEnvironmentVariable("TELEGRAM_CHAT_ID","...","User")
```

---

## 매일 자동 실행

### 방법 A — GitHub Actions (권장, PC 전원 무관)

1. 이 저장소를 GitHub에 push
2. Settings → Secrets and variables → Actions → **New repository secret** 로 2개 등록
   - `TELEGRAM_BOT_TOKEN` — 봇 토큰
   - `TELEGRAM_CHAT_ID` — chat id
3. Actions 탭에서 **부동산 일일 리포트** → `Run workflow`로 수동 테스트
4. 정상이면 이후 매일 KST 09:00에 자동 실행됩니다

> GitHub Actions cron은 5~30분 지연될 수 있습니다. 정시성이 중요하면
> `.github/workflows/daily.yml`의 cron을 `0 23 * * *`(KST 08:00)로 앞당기세요.

> 데이터센터 IP에서 네이버가 `429`를 반복한다면 방법 B로 전환하세요.
> 스크립트는 동일하므로 전환 비용이 없습니다.

### 방법 B — Windows 작업 스케줄러 (PC가 켜져 있어야 함)

관리자 PowerShell에서:

```powershell
$action  = New-ScheduledTaskAction -Execute "powershell.exe" `
  -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$PWD\scripts\run_daily.ps1`""
$trigger = New-ScheduledTaskTrigger -Daily -At 9:00AM
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable
Register-ScheduledTask -TaskName "부동산 일일 리포트" `
  -Action $action -Trigger $trigger -Settings $settings
```

`-StartWhenAvailable`은 9시에 PC가 꺼져 있었다면 다음 부팅 직후 실행합니다.
토큰은 사용자 환경변수 `TELEGRAM_BOT_TOKEN`에 등록해 두세요.
로그는 `logs/YYYY-MM-DD.log`에 쌓입니다.

---

## 명령행 옵션

| 옵션 | 설명 |
|---|---|
| `--dry-run` | 텔레그램으로 보내지 않고 콘솔에만 출력 |
| `--no-save` | `data/snapshot.json`을 갱신하지 않음 (테스트용) |
| `--skip-if-done` | 오늘 이미 성공했으면 즉시 종료 (하루 여러 번 예약 실행용) |

### 테스트

네트워크·텔레그램을 타지 않는 순수 로직만 검증합니다. 파일 하나가 곧 실행 단위입니다
(pytest 로도 돌아갑니다).

```bash
python tests/test_retry.py && python tests/test_state.py
```
| — | `docs/index.html`은 두 옵션과 무관하게 항상 갱신됩니다 |
| `--config PATH` | 다른 설정 파일 사용 |

---

## 동작 참고

- **첫 실행**은 비교 대상이 없으므로 전체 매물이 `🆕`로 표시됩니다. 정상입니다.
- 상태는 `data/snapshot.json` 하나에만 저장됩니다. DB가 필요 없고,
  git 히스토리가 그대로 **가격 시계열 기록**이 됩니다.
- 네이버가 호출 빈도에 민감해 단지 간 25초, 워밍업 8초를 대기합니다.
  단지 5개면 약 3분 걸립니다. 정상 동작이니 기다리세요.
- 한 단지가 실패해도 나머지는 보고되며, 실패 내역이 리포트 하단에 붙습니다.
  **전부** 실패한 날은 스냅샷을 덮어쓰지 않습니다(다음 날 전건 신규 오보 방지).
- 동일 매물을 여러 중개사가 올린 경우 네이버가 이미 1건으로 묶어 주며,
  `중개사N` 표기로 몇 곳이 광고 중인지 보여줍니다.

---

## 429 (`Too Many Requests`)가 뜰 때

네이버는 **실행 IP 단위로** 매물 API 를 차단합니다. 걸리면 쿠키를 새로 받든 헤더를
바꾸든 `front-api` 전체가 즉시 429 로 응답하고(HTML 페이지는 멀쩡합니다),
**40분이 지나도 안 풀립니다**(실측). 코드로 우회할 방법은 없습니다.

그래서 대응이 두 층입니다.

- **짧은 재시도** (1분 → 3분, 총 8분 한도) — 순간적인 네트워크 오류만 흡수합니다.
  차단을 뚫으려는 게 아닙니다. 같은 실행은 IP 가 그대로라 오래 기다려도 소용없습니다.
- **하루 3번 실행** (08:37 / 12:23 / 19:51) — 실행마다 러너 IP 가 새로 배정되므로,
  아침에 막혀도 점심·저녁에 뚫릴 수 있습니다. **한 번 성공하면 그날 나머지 실행은
  즉시 종료**하고, 실패 알림도 하루 한 번만 갑니다.

| 증상 | 원인과 대응 |
|---|---|
| GitHub Actions 에서만 429 | 공용 러너 IP 가 다른 사용자들 탓에 이미 차단된 상태. 하루 3번 중 한 번이라도 뚫리면 됩니다. 며칠 내내 3번 다 막히면 **작업 스케줄러로 전환**하세요 |
| 로컬에서 429 또는 응답 없음 | 짧은 시간에 여러 번 돌린 경우. 한 시간쯤 쉬면 풀립니다 |
| `ReadTimeout` 만 반복 | 차단의 다른 얼굴입니다(429 대신 무응답). 같은 상황이니 똑같이 기다리세요 |

### 작업 스케줄러로 전환

집 IP 는 차단될 일이 거의 없어 이쪽이 훨씬 안정적입니다. PC 가 그 시각에 켜져
있어야 한다는 점만 다릅니다.

```powershell
schtasks /create /tn "부동산리포트" /tr "powershell -ExecutionPolicy Bypass -File \"C:\Users\yslee\Python Projects\Project_real-estate-monitor\scripts\run_daily.ps1\"" /sc daily /st 09:00
```

전환하면 `.github/workflows/daily.yml` 의 `schedule:` 을 지워 Actions 쪽 실패
알림을 끄세요 (`workflow_dispatch:` 는 남겨 두면 "지금 갱신" 버튼이 계속 됩니다).

## 유의

네이버 부동산 이용약관·robots.txt는 자동 수집을 제한합니다. 이 도구는 개인이
본인 관심 단지 소수를 **하루 1회** 조회하는 용도를 전제로 하며, 위 호출 간격을
지킵니다. 공개 배포·상업적 이용은 범위 밖입니다.

# 네이버 부동산 → 텔레그램 일일 리포트

매일 아침 9시에 지정한 아파트 단지들의 매물을 수집해, 전일 대비 **신규 / 가격변동 / 소진**을 텔레그램으로 보고한다.

- 💰 비용 **0원** (외부 서버·DB·유료 API 없음)
- 📦 의존성 2개 (`requests`, `PyYAML`)
- 🗄️ 상태 저장은 JSON 파일 1개 — 별도 DB 없음

---

## 1. 데이터 소스

네이버 부동산은 2025년 이후 `fin.land.naver.com`(Npay 부동산)으로 이전되었다.
기존에 널리 쓰이던 `m.land.naver.com/complex/getComplexArticleList` 및
`new.land.naver.com/api/*` 는 **모두 폐지**되었다 (302 리다이렉트 / 404).

### 1.1 현행 엔드포인트

```
① GET  https://fin.land.naver.com/complexes/{complexNumber}
       → 세션 쿠키 PROP_TEST_KEY / PROP_TEST_ID 획득 (워밍업)

② POST https://fin.land.naver.com/front-api/v1/complex/article/list
       Cookie: PROP_TEST_KEY=...; PROP_TEST_ID=...
       Content-Type: application/json
       Referer: https://fin.land.naver.com/complexes/{complexNumber}
       Origin:  https://fin.land.naver.com

       {
         "complexNumber": "8692",
         "tradeTypes": ["A1"],
         "size": 30,
         "userChannelType": "PC",
         "articleSortType": "PRICE_ASC"
       }
```

### 1.2 반드시 지켜야 할 3가지 (실측 확인)

| # | 제약 | 위반 시 |
|---|---|---|
| 1 | **①의 쿠키 워밍업을 먼저 수행**해야 한다 | `429 TOO_MANY_REQUESTS` |
| 2 | `complexNumber`는 **문자열** (`"8692"`) | `400` — zod 스키마가 `/^\d{1,9}$/` 문자열을 요구 |
| 3 | 페이징 파라미터는 **평면 구조** (`articlePagingRequest`로 감싸지 말 것) | `400` |

### 1.3 파라미터

| 항목 | 값 |
|---|---|
| `tradeTypes` | `A1` 매매 · `B1` 전세 · `B2` 월세 · `B3` 단기임대 |
| `articleSortType` | `PRICE_ASC` `PRICE_DESC` `DATE_DESC` `SPACE_ASC` `SPACE_DESC` `RANKING_DESC` |
| `size` | 1–30 (초과분은 `seed` + `lastInfo` 커서 페이징) |

### 1.4 응답 구조 (발췌)

```
result.totalCount / result.hasNextPage / result.seed / result.lastInfo
result.list[].representativeArticleInfo
    ├ articleNumber, complexName, dongName, tradeType
    ├ spaceInfo        : exclusiveSpace(전용㎡), supplySpace(공급㎡)
    ├ priceInfo        : dealPrice, warrantyPrice, rentPrice, managementFeeAmount
    ├ articleDetail    : floorInfo("10/20"), direction, articleFeatureDescription
    ├ verificationInfo : articleConfirmDate, verificationType
    └ brokerInfo       : brokerageName
result.list[].duplicatedArticleInfo   (선택적)
    └ realtorCount     : 동일 매물을 광고 중인 중개사 수
```

네이버가 **동일 매물의 중복 광고를 이미 그룹핑**해서 대표 매물 1건으로 내려주므로,
별도의 중복 제거 로직이 필요 없다.

### 1.5 호출 페이싱

수 초 간격 연속 호출 시 즉시 `429`가 발생한다. 따라서:

```
단지마다:  쿠키 워밍업 GET → 8초 → 매물 API POST → (다음 단지 전) 25초
```

단지 5개 기준 약 3분. 하루 1회 실행에는 무리가 없다.

### 1.6 유의사항

네이버 부동산 이용약관·robots.txt는 자동 수집을 제한한다. 본 도구는 **개인이 본인 관심
단지 소수를 하루 1회 조회**하는 용도를 전제로 하며, 위 페이싱을 준수한다. 공개 배포나
상업적 이용은 범위 밖이다.

---

## 2. 아키텍처

```
config.yaml                     감시 대상 단지 / 거래유형 / 평형대
        │
        ▼
src/naver.py     ── 쿠키 워밍업 → 매물 API → Article 정규화
        │
        ▼
src/filters.py   ── 평형대(공급면적 기준) · 가격 상한 필터
        │
        ▼
src/diff.py      ── data/snapshot.json 과 비교 → 신규/가격변동/소진
        │
        ▼
src/report.py    ── 텔레그램 메시지 텍스트 생성 (4096자 분할)
        │
        ├──────────────┐
        ▼              ▼
src/telegram.py   src/htmlgen.py  ── docs/index.html (GitHub Pages)
   Bot API 전송      정렬·필터 가능한 표
   (토큰 없으면
    콘솔 출력)
        │
        ▼
data/snapshot.json  갱신 → git commit
```

### 2.0 두 가지 출력

| | 텔레그램 | HTML 대시보드 |
|---|---|---|
| 용도 | 아침에 **변화만** 빠르게 확인 | 필요할 때 **전체 매물**을 훑어보기 |
| 내용 | 신규·변동·소진 요약 | 전 매물 표 + 거래유형/평형 선택 |
| 경로 | Bot API | `docs/index.html` → GitHub Pages |

### 2.0.1 수집은 넓게, 선택은 UI에서

거래유형(매매/전세)과 평형대는 **수집 단계에서 좁히지 않는다**. 좁혀서 저장하면
나중에 UI에서 다시 넓힐 수 없기 때문이다. `config.yaml`은 넓게 열어두고
(`trade_types: [매매, 전세]`, `pyeong_groups: []`), 실제 선택은 대시보드에서 한다.
`price_min/max`는 명시적으로 관심 밖 매물을 버리고 싶을 때만 쓴다.

표의 모든 행은 `data-trade` / `data-group` / `data-state` 속성을 갖고,
필터는 행을 숨기기만 하며 통계는 보이는 행에서 다시 계산한다.

### 2.0.2 "지금 갱신" 버튼의 한계

정적 페이지에서 브라우저가 네이버 API를 직접 호출하는 것은 불가능하다.
`fin.land.naver.com`이 CORS 헤더를 주지 않고, 세션 쿠키도 필요하기 때문이다.
서버를 두면 되지만 그 순간 이 설계의 "비용 0원" 전제가 깨진다.

대신 버튼은 **Actions 실행 화면으로 연결**한다. 거기서 `Run workflow`를 누르면
워크플로가 수집 → `docs/index.html` 재생성 → 커밋까지 수행하고,
GitHub Pages가 1~2분 내 반영한다. 클릭 두 번이지만 추가 비용과 인증 노출이 없다.

> 페이지에서 곧바로 워크플로를 트리거하려면 GitHub PAT가 필요한데,
> public 저장소의 정적 파일에 토큰을 넣는 것은 자격증명 유출이므로 채택하지 않는다.

HTML은 외부 CDN·폰트·스크립트를 참조하지 않는 **단일 자족 파일**로 생성한다.
파일 하나만 열면 어디서든 동작하고, GitHub Pages의 `/docs` 폴더 게시와 그대로 맞물린다.

### 2.1 상태 저장을 JSON 파일로 하는 이유

매물은 단지당 수십 건 규모라 DB가 과하다. JSON 파일 하나면 충분하고,
**git 히스토리가 그대로 가격 시계열 기록**이 되어 나중에 추이 분석을 공짜로 얻는다.
외부 DB 계정·키·무료티어 만료 관리가 사라지는 것이 이 설계의 핵심 절약 포인트다.

### 2.2 평형대 분류

API에 평형 필터가 없으므로 클라이언트에서 계산한다.
한국에서 "24평형/33평형"이라 부르는 값은 **공급면적** 기준이다.

```
평형 = supplySpace ÷ 3.3058
평형대 = (평형을 내림한 정수) // 10 * 10      예) 32.9평 → 30평대
```

### 2.3 매물 식별과 diff

`articleNumber`를 키로 사용한다.

| 구분 | 판정 |
|---|---|
| 🆕 신규 | 이전 스냅샷에 없던 `articleNumber` |
| 💸 가격변동 | 동일 `articleNumber`의 가격 필드가 변경 |
| ❌ 소진 | 이전 스냅샷에 있었으나 이번에 없음 |

---

## 3. 설정

```yaml
telegram:
  chat_id: "123456789"          # 토큰은 환경변수 TELEGRAM_BOT_TOKEN

defaults:
  trade_types: [매매]            # 매매 / 전세 / 월세 / 단기임대
  pyeong_groups: [20, 30]       # 공급면적 기준 평형대
  price_max: null               # 만원 단위, null이면 제한 없음

complexes:
  - name: 성복역현대홈타운
    number: "8692"
```

- `defaults` + 단지별 override 구조라 단지를 늘려도 설정이 짧게 유지된다.
- 전세를 함께 보려면 `trade_types: [매매, 전세]` 한 줄만 고치면 된다.

---

## 4. 리포트 형태

```
🏠 부동산 리포트 · 2026-07-31 (금)

━━ 성복역현대홈타운 ━━
💰 매매

[30평대] 13건 (▲1) · 10.00~12.30억
  🆕 205동 10/20층 · 11.50억
     샷시포함확장완전특올수리, 성복역초역세권
  💸 202동 중/20층 · 11.50억 → 11.20억 (-3,000만)
  ❌ 소진 1건

[20평대] 5건 (—) · 10.00~11.00억
     변동 없음
```

신규/가격변동/소진만 상세 표시하고 나머지는 요약해 메시지 길이를 억제한다.

---

## 5. 실행 환경

| | GitHub Actions | Windows 작업 스케줄러 |
|---|---|---|
| 비용 | 0원 | 0원 |
| PC 전원 | 무관 ✅ | 9시에 켜져 있어야 함 |
| 차단 리스크 | 미검증 (데이터센터 IP) | 낮음 |
| 정시성 | 5~30분 지연 가능 | 정확 |

**GitHub Actions를 먼저 시도**하고, `429`가 반복되면 작업 스케줄러로 전환한다.
스크립트는 동일하므로 전환 비용이 사실상 없다.

- cron: `0 0 * * *` (UTC 00:00 = KST 09:00)
- 스냅샷은 워크플로가 자동 커밋 → 60일 무활동으로 인한 Actions 비활성화도 자동 회피

---

## 6. 준비물

| 항목 | 방법 |
|---|---|
| 봇 토큰 | `@BotFather` → `/newbot` |
| chat_id | 봇에게 메시지 전송 후 `api.telegram.org/bot<TOKEN>/getUpdates` |
| GitHub Secret | repo Settings → Secrets → `TELEGRAM_BOT_TOKEN` |
| 단지번호 | `fin.land.naver.com/complexes/{번호}` URL의 숫자 |

---

## 7. 실패 처리

- 단지 단위로 예외를 격리해, 한 단지가 실패해도 나머지는 정상 보고한다.
- 수집이 **전부** 실패하면 스냅샷을 덮어쓰지 않는다 (다음 날 전건 신규로 오보되는 것 방지).
- 실패 내역은 리포트 하단에 함께 전송해 조용히 죽는 상황을 막는다.

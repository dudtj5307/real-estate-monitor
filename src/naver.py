"""네이버 부동산(fin.land.naver.com) 매물 수집.

주의 (DESIGN.md 1.2 참고):
  1. 매물 API 호출 전 단지 페이지를 GET 해서 세션 쿠키를 받아야 한다. 없으면 429.
  2. complexNumber 는 문자열이어야 한다. 숫자면 400.
  3. 페이징 파라미터는 평면 구조여야 한다. 감싸면 400.
"""

from __future__ import annotations

import random
import sys
import time
from dataclasses import dataclass, asdict
from typing import Any

import requests

BASE = "https://fin.land.naver.com"
ARTICLE_LIST_URL = f"{BASE}/front-api/v1/complex/article/list"

# UA 는 실행마다 하나를 골라 쓴다. 이미 걸린 차단을 푸는 효과는 없지만
# (DESIGN.md 1.5), 매 실행 같은 지문을 남기지 않는 정도의 값은 있다.
USER_AGENTS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
)

TRADE_TYPES = {
    "매매": "A1",
    "전세": "B1",
    "월세": "B2",
    "단기임대": "B3",
}
TRADE_TYPES_KO = {v: k for k, v in TRADE_TYPES.items()}

PYEONG_PER_SQM = 3.3058

# 페이싱: 연속 호출하면 429가 걸린다
WARMUP_DELAY = 8.0
COMPLEX_DELAY = 25.0
PAGE_DELAY = 25.0

# 429 는 호출 속도가 아니라 IP 단위로 걸린다 (실측: 차단 중에는 front-api 전체가
# 0.03초 만에 429 즉답 — 헤더·쿠키를 바꿔도 소용없다). 게다가 한 번 걸리면
# **40분이 지나도 안 풀린다**(2026-08-01 실측, 5분 간격 43분 관측 내내 429).
#
# ⚠ 그래서 재시도는 차단을 뚫는 수단이 아니다. 실측 로그에서 실행 IP 는 **첫 요청부터**
# 429 를 받았다 — 우리가 아무것도 하기 전에 이미 오염된 IP 였다는 뜻이고, 그 IP 는
# 한참 뒤까지 안 풀린다. 거기서 8분을 재시도로 태우면 성공률은 그대로인 채
# 차단된 IP 만 계속 두드리게 된다.
#
# 수집을 집 라즈베리파이로 옮기면서 전제 하나가 바뀌었다: **IP 가 더는 바뀌지 않는다.**
# 러너 시절엔 다음 예약 실행이 새 IP 를 받아 저절로 복구됐지만, 집 IP 는 하나뿐이라
# 기다리는 것 말고 회복 수단이 없다. 그래서 두드리지 않는 게 전보다 더 중요하다.
# 하루 한 번 다른 IP 로 시도하는 경로는 watchdog 폴백(공용 러너)뿐이다.
#
# 상황을 둘로 나누는 것 자체는 그대로 옳다.
#   ① 이 실행에서 아직 한 번도 성공하지 못했다 → 실행 IP 자체가 막힌 것이다.
#      재시도하지 않고 즉시 포기(IPBlocked). 12:30 요청이나 watchdog 이 덮는다.
#   ② 앞 단지는 됐는데 다음 요청이 막혔다 → 우리 호출 속도 탓일 수 있다.
#      이때만 짧게 쉬었다 재시도한다.
RETRY_WAITS = (45.0, 120.0)
RETRY_JITTER = 0.15  # ±15% — 여러 실행이 같은 시각에 몰리지 않게

# 재시도에 쓸 수 있는 전체 시간. 단지마다 재시도를 다 쓰면 실행 시간이 단지 수에
# 비례해 늘어나 워크플로 timeout-minutes 를 넘긴다. 그래서 예산을 공유한다.
RETRY_BUDGET = 4 * 60.0


class NaverError(RuntimeError):
    pass


class RateLimited(NaverError):
    """429. 재시도로 풀릴 수 있는 일시적 차단."""


class IPBlocked(RateLimited):
    """이 실행의 첫 요청부터 막혔다 = 실행 IP 가 통째로 차단된 상태.

    나머지 단지를 시도해 봐야 같은 IP 라 똑같이 막힌다. 호출자는 이걸 받으면
    남은 단지를 건너뛰고 실행을 끝내야 한다 (main.py).
    """


# 차단은 429 로만 오지 않는다. 실측: 같은 차단 상태에서 curl 은 429 즉답을 받는데
# requests 는 응답이 아예 안 와 ReadTimeout 이 난다(연결만 물고 늘어지는 형태).
# 그래서 타임아웃·연결 실패도 429 와 같은 재시도 경로를 타야 한다.
RETRYABLE = (RateLimited, requests.Timeout, requests.ConnectionError)


@dataclass
class Article:
    """정규화된 매물 1건."""

    article_number: str
    complex_name: str
    trade_type: str  # 매매 / 전세 / ...
    dong: str
    floor: str  # "10/20", "중/20"
    exclusive_sqm: float
    supply_sqm: float
    price: int  # 만원. 매매=매매가, 전세=보증금, 월세=보증금
    rent: int  # 만원. 월세만 사용
    direction: str
    feature: str
    broker: str
    confirm_date: str
    realtor_count: int

    @property
    def pyeong(self) -> float:
        """공급면적 기준 평형."""
        return self.supply_sqm / PYEONG_PER_SQM

    @property
    def pyeong_group(self) -> int:
        """20평대 -> 20, 30평대 -> 30."""
        return int(self.pyeong) // 10 * 10

    @property
    def url(self) -> str:
        return f"{BASE}/articles/{self.article_number}"

    def price_key(self) -> tuple[int, int]:
        """가격 변동 판정용 키."""
        return (self.price, self.rent)

    def location_key(self) -> tuple[str, str, str, int]:
        """매물번호가 바뀌어도 같은 집임을 알아보기 위한 위치 키 (diff.py ② 참고).

        articleNumber 는 중복 묶음의 '대표'라 대표가 바뀌면 같이 바뀐다.
        위치는 그대로이므로 이걸로 다시 짝지어 재등록을 가격변동으로 판정한다.
        전용면적은 부동소수 오차를 피해 ㎡ 를 100배 정수로 만든다.
        """
        return (self.trade_type, self.dong, self.floor,
                round(self.exclusive_sqm * 100))

    def has_location(self) -> bool:
        """위치 키를 신뢰할 수 있는가. 한 칸이라도 비면 서로 다른 집이 뭉친다."""
        return bool(self.dong and self.floor and self.exclusive_sqm)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Article":
        return cls(**d)


def _to_manwon(won: int | None) -> int:
    return int(won or 0) // 10000


def _parse_article(item: dict[str, Any]) -> Article | None:
    info = item.get("representativeArticleInfo")
    if not info:
        return None

    space = info.get("spaceInfo") or {}
    price = info.get("priceInfo") or {}
    detail = info.get("articleDetail") or {}
    verify = info.get("verificationInfo") or {}
    broker = info.get("brokerInfo") or {}
    dup = item.get("duplicatedArticleInfo") or {}

    trade_code = info.get("tradeType", "")
    trade_ko = TRADE_TYPES_KO.get(trade_code, trade_code)

    # 매매는 dealPrice, 전세/월세는 warrantyPrice 가 본 가격
    deal = _to_manwon(price.get("dealPrice"))
    warranty = _to_manwon(price.get("warrantyPrice"))
    main_price = deal if trade_code == "A1" else warranty

    return Article(
        article_number=str(info.get("articleNumber", "")),
        complex_name=info.get("complexName", ""),
        trade_type=trade_ko,
        dong=info.get("dongName", ""),
        floor=detail.get("floorInfo", ""),
        exclusive_sqm=float(space.get("exclusiveSpace") or 0),
        supply_sqm=float(space.get("supplySpace") or 0),
        price=main_price,
        rent=_to_manwon(price.get("rentPrice")),
        direction=detail.get("directionStandard", ""),
        feature=(detail.get("articleFeatureDescription") or "").strip(),
        broker=broker.get("brokerageName", ""),
        confirm_date=verify.get("articleConfirmDate", ""),
        realtor_count=int(dup.get("realtorCount") or 1),
    )


class NaverClient:
    """단지 하나당 세션을 워밍업하고 매물 목록을 가져온다."""

    def __init__(self, *, warmup_delay: float = WARMUP_DELAY, page_delay: float = PAGE_DELAY,
                 retry_waits: tuple[float, ...] = RETRY_WAITS,
                 retry_budget: float = RETRY_BUDGET):
        self.warmup_delay = warmup_delay
        self.page_delay = page_delay
        self.retry_waits = retry_waits
        # 모든 단지가 나눠 쓰는 재시도 예산 (초). 남은 시간이 모자라면 재시도하지 않는다.
        self.retry_left = retry_budget
        # 이 실행에서 매물 API 가 정상 응답한 횟수. 0 이면 IP 차단으로 보고
        # 재시도 없이 즉시 포기한다 (아래 RETRY_WAITS 주석 ①).
        self.ok_count = 0
        self.user_agent = random.choice(USER_AGENTS)
        self.session: requests.Session = None  # type: ignore[assignment]
        self._new_session()

    def _new_session(self) -> None:
        """쿠키를 버리고 세션을 새로 만든다. 429 재시도 전에 호출한다."""
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": self.user_agent,
            "Accept-Language": "ko-KR,ko;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
        })

    def _warmup(self, complex_number: str) -> None:
        """단지 페이지를 방문해 PROP_TEST_* 쿠키를 받는다. 없으면 API가 429를 준다."""
        url = f"{BASE}/complexes/{complex_number}"
        res = self.session.get(url, timeout=15)
        if res.status_code == 429:
            # 워밍업까지 막히면 확실한 IP 차단. POST 와 같은 경로로 재시도시킨다.
            raise RateLimited("429 Too Many Requests (워밍업)")
        res.raise_for_status()
        time.sleep(self.warmup_delay)

    def _post_article_list(self, complex_number: str, trade_code: str,
                           seed: str | None, last_info: list | None) -> dict[str, Any]:
        body: dict[str, Any] = {
            "complexNumber": str(complex_number),  # 반드시 문자열
            "tradeTypes": [trade_code],
            "size": 30,
            "userChannelType": "PC",
            "articleSortType": "PRICE_ASC",
        }
        # 커서 페이징 파라미터도 평면으로 넣는다
        if seed:
            body["seed"] = seed
        if last_info:
            body["lastInfo"] = last_info

        res = self.session.post(
            ARTICLE_LIST_URL,
            json=body,
            headers={
                "Referer": f"{BASE}/complexes/{complex_number}",
                "Origin": BASE,
                "Accept": "application/json, text/plain, */*",
                "sec-fetch-site": "same-origin",
                "sec-fetch-mode": "cors",
                "sec-fetch-dest": "empty",
                "priority": "u=1, i",
            },
            timeout=20,
        )
        if res.status_code == 429:
            raise RateLimited("429 Too Many Requests")
        if res.status_code != 200:
            raise NaverError(f"HTTP {res.status_code}: {res.text[:200]}")

        data = res.json()
        if not data.get("isSuccess", True):
            raise NaverError(f"API 오류: {data.get('detailCode')} {data.get('message')}")

        # 매물 API 가 한 번이라도 정상 응답했다 = 이 IP 는 차단 상태가 아니다.
        # 이후의 429 는 IP 차단이 아니라 우리 호출 속도 문제로 본다 (fetch 참고).
        self.ok_count += 1
        return data.get("result") or {}

    def fetch(self, complex_number: str, trade_types: list[str]) -> list[Article]:
        """한 단지의 매물을 가져온다.

        아직 이 실행에서 한 건도 못 받아왔다면 재시도하지 않는다 — 실행 IP 가
        통째로 막힌 상태이므로 이 실행 안에서는 안 풀린다(IPBlocked).
        앞 단지가 성공한 뒤의 429 는 우리 호출 속도 탓일 수 있어 짧게 재시도한다.
        """
        last: Exception | None = None
        tried = 0

        # 첫 성공 전에는 재시도하지 않는다 (위 주석 ①)
        waits = self.retry_waits if self.ok_count else ()

        for attempt in range(len(waits) + 1):
            if attempt:
                wait = waits[attempt - 1]
                wait *= 1.0 + random.uniform(-RETRY_JITTER, RETRY_JITTER)
                if wait > self.retry_left:
                    print(
                        f"[중단] 재시도 예산 소진 — 남은 {self.retry_left / 60:.1f}분",
                        file=sys.stderr, flush=True,
                    )
                    break
                print(
                    f"[대기] 차단 추정({type(last).__name__}) — "
                    f"{wait / 60:.1f}분 후 재시도 ({attempt}/{len(waits)})",
                    file=sys.stderr, flush=True,
                )
                time.sleep(wait)
                self.retry_left -= wait
                tried = attempt
                self._new_session()  # 쿠키를 버리고 워밍업부터 다시

            try:
                return self._fetch_once(complex_number, trade_types)
            except RETRYABLE as exc:
                last = exc
                print(f"[실패] {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)

        if self.ok_count == 0:
            # 이 실행의 첫 요청부터 막혔다 = 실행 IP 가 이미 차단된 상태.
            # 나머지 단지도 볼 것 없이 끝낸다. 집 IP 는 바뀌지 않으므로 회복은
            # 시간이 지나기를 기다리는 것뿐이다 (또는 watchdog 폴백의 러너 IP).
            raise IPBlocked(
                f"첫 요청부터 차단됨({type(last).__name__}) — 실행 IP 가 이미 "
                f"네이버에 막혀 있습니다. 같은 IP 로 재시도해도 풀리지 않습니다"
            ) from last
        if isinstance(last, RateLimited):
            raise RateLimited(
                f"429 Too Many Requests — 재시도 {tried}회 후에도 차단 "
                f"(IP 단위 차단이라 계속되면 단지 수를 줄이거나 며칠 쉬어야 합니다)"
            ) from last
        raise RateLimited(
            f"응답 없음 — 재시도 {tried}회 후에도 실패 ({type(last).__name__}). "
            f"차단 중에는 429 대신 무응답으로 나타나기도 합니다"
        ) from last

    def _fetch_once(self, complex_number: str, trade_types: list[str]) -> list[Article]:
        """워밍업 → 거래유형별 수집. 재시도 없이 1회만."""
        self._warmup(complex_number)

        articles: list[Article] = []
        for idx, trade_ko in enumerate(trade_types):
            trade_code = TRADE_TYPES.get(trade_ko)
            if not trade_code:
                raise NaverError(f"알 수 없는 거래유형: {trade_ko}")
            if idx > 0:
                time.sleep(self.page_delay)
            articles.extend(self._fetch_one_trade(complex_number, trade_code))
        return articles

    def _fetch_one_trade(self, complex_number: str, trade_code: str) -> list[Article]:
        out: list[Article] = []
        seed: str | None = None
        last_info: list | None = None

        while True:
            result = self._post_article_list(complex_number, trade_code, seed, last_info)
            for item in result.get("list") or []:
                article = _parse_article(item)
                if article:
                    out.append(article)

            if not result.get("hasNextPage"):
                break
            seed = result.get("seed")
            last_info = result.get("lastInfo")
            if not last_info:
                break
            time.sleep(self.page_delay)

        return out

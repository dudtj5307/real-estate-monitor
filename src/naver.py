"""네이버 부동산(fin.land.naver.com) 매물 수집.

주의 (DESIGN.md 1.2 참고):
  1. 매물 API 호출 전 단지 페이지를 GET 해서 세션 쿠키를 받아야 한다. 없으면 429.
  2. complexNumber 는 문자열이어야 한다. 숫자면 400.
  3. 페이징 파라미터는 평면 구조여야 한다. 감싸면 400.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, asdict
from typing import Any

import requests

BASE = "https://fin.land.naver.com"
ARTICLE_LIST_URL = f"{BASE}/front-api/v1/complex/article/list"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
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


class NaverError(RuntimeError):
    pass


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

    def __init__(self, *, warmup_delay: float = WARMUP_DELAY, page_delay: float = PAGE_DELAY):
        self.warmup_delay = warmup_delay
        self.page_delay = page_delay
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": UA,
            "Accept-Language": "ko-KR,ko;q=0.9",
        })

    def _warmup(self, complex_number: str) -> None:
        """단지 페이지를 방문해 PROP_TEST_* 쿠키를 받는다. 없으면 API가 429를 준다."""
        url = f"{BASE}/complexes/{complex_number}"
        res = self.session.get(url, timeout=15)
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
            },
            timeout=20,
        )
        if res.status_code == 429:
            raise NaverError("429 Too Many Requests — 호출 간격을 늘리거나 잠시 후 재시도하세요")
        if res.status_code != 200:
            raise NaverError(f"HTTP {res.status_code}: {res.text[:200]}")

        data = res.json()
        if not data.get("isSuccess", True):
            raise NaverError(f"API 오류: {data.get('detailCode')} {data.get('message')}")
        return data.get("result") or {}

    def fetch(self, complex_number: str, trade_types: list[str]) -> list[Article]:
        """한 단지의 지정 거래유형 매물을 모두 가져온다."""
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

"""전용면적 · 평형대 · 가격 필터."""

from __future__ import annotations

from .config import ComplexConfig
from .naver import Article


def apply(articles: list[Article], cfg: ComplexConfig) -> list[Article]:
    out = articles

    # 전용면적(㎡) 범위. 평형대(공급면적)보다 우선하는 1차 기준이다.
    # exclusive_sqm 이 0 인 매물은 면적 미상이므로 범위를 걸면 빠진다.
    if cfg.area_min is not None:
        out = [a for a in out if a.exclusive_sqm >= cfg.area_min]

    if cfg.area_max is not None:
        out = [a for a in out if a.exclusive_sqm <= cfg.area_max]

    if cfg.pyeong_groups:
        allowed = set(cfg.pyeong_groups)
        out = [a for a in out if a.pyeong_group in allowed]

    if cfg.price_min is not None:
        out = [a for a in out if a.price >= cfg.price_min]

    if cfg.price_max is not None:
        out = [a for a in out if a.price <= cfg.price_max]

    return out

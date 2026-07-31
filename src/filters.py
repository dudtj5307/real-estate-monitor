"""평형대 · 가격 필터."""

from __future__ import annotations

from .config import ComplexConfig
from .naver import Article


def apply(articles: list[Article], cfg: ComplexConfig) -> list[Article]:
    out = articles

    if cfg.pyeong_groups:
        allowed = set(cfg.pyeong_groups)
        out = [a for a in out if a.pyeong_group in allowed]

    if cfg.price_min is not None:
        out = [a for a in out if a.price >= cfg.price_min]

    if cfg.price_max is not None:
        out = [a for a in out if a.price <= cfg.price_max]

    return out

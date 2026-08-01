"""config.yaml 로드 및 단지별 설정 병합."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class ComplexConfig:
    name: str
    number: str
    trade_types: list[str]
    pyeong_groups: list[int]
    price_min: int | None
    price_max: int | None


@dataclass
class Config:
    chat_id: str
    repo: str = ""
    complexes: list[ComplexConfig] = field(default_factory=list)


_DEFAULTS: dict[str, Any] = {
    "trade_types": ["매매"],
    "pyeong_groups": [],
    "price_min": None,
    "price_max": None,
}


def load(path: str | Path) -> Config:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}

    defaults = dict(_DEFAULTS)
    defaults.update(raw.get("defaults") or {})

    complexes = []
    for entry in raw.get("complexes") or []:
        merged = dict(defaults)
        merged.update({k: v for k, v in entry.items() if v is not None})
        complexes.append(
            ComplexConfig(
                name=merged["name"],
                number=str(merged["number"]),
                trade_types=list(merged["trade_types"]),
                pyeong_groups=list(merged["pyeong_groups"] or []),
                price_min=merged["price_min"],
                price_max=merged["price_max"],
            )
        )

    if not complexes:
        raise ValueError("config.yaml 에 complexes 가 비어 있습니다")

    # 저장소가 public일 수 있으므로 환경변수를 우선한다.
    # config.yaml 의 chat_id 는 로컬 편의용 폴백.
    chat_id = os.environ.get("TELEGRAM_CHAT_ID") or (raw.get("telegram") or {}).get("chat_id") or ""

    return Config(
        chat_id=str(chat_id),
        repo=str((raw.get("site") or {}).get("repo") or ""),
        complexes=complexes,
    )

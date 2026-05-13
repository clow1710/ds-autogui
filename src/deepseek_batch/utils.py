from __future__ import annotations

from datetime import datetime
from typing import Any


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def enum_value(cls: Any, enum_name: str, item_name: str) -> Any:
    enum_cls = getattr(cls, enum_name, None)
    if enum_cls is not None and hasattr(enum_cls, item_name):
        return getattr(enum_cls, item_name)
    return getattr(cls, item_name)


"""Load task-local secret environment values at the executable boundary."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Final

from pydantic import TypeAdapter, ValidationError

if TYPE_CHECKING:
    from pathlib import Path

KEYS: Final = frozenset({"AMAP_MAPS_API_KEY", "NAV_WIFI_PASSWORD"})
STRING: Final = TypeAdapter(str)
QUOTE_PAIR_LENGTH: Final = 2


def decode_value(value: str) -> str:
    """Decode a serialized dotenv string while preserving entered credential bytes."""
    quoted = value.strip()
    if len(quoted) >= QUOTE_PAIR_LENGTH and quoted[0] == quoted[-1] == '"':
        try:
            return STRING.validate_json(quoted)
        except ValidationError:
            return quoted[1:-1]
    if len(quoted) >= QUOTE_PAIR_LENGTH and quoted[0] == quoted[-1] == "'":
        return quoted[1:-1]
    return value


def load(path: Path) -> None:
    """Load explicitly named environment variables from a local env file."""
    for key, value in read(path, KEYS).items():
        _ = os.environ.setdefault(key, value)


def read(path: Path, names: frozenset[str]) -> dict[str, str]:
    """Read only the requested task-local values."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text().splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip() in names:
            _ = values.setdefault(key.strip(), decode_value(value))
    return values

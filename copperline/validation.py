"""Input normalization at service boundaries."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from .errors import ValidationError

_CODE = re.compile(r"[A-Z0-9][A-Z0-9._-]{1,47}\Z")
MAX_QTY = 2**63 - 1


def label(value: Any, name: str, *, max_length: int = 160) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{name} must be text")
    value = value.strip()
    if not value or len(value) > max_length or any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValidationError(f"{name} must be 1-{max_length} printable characters")
    return value


def optional_note(value: Any) -> str:
    if not isinstance(value, str) or len(value.strip()) > 240:
        raise ValidationError("Note must be text of at most 240 characters")
    value = value.strip()
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValidationError("Note cannot contain control characters")
    return value


def code(value: Any, name: str = "Code") -> str:
    result = label(value, name, max_length=48).upper()
    if not _CODE.fullmatch(result):
        raise ValidationError(f"{name} must use 2-48 letters, digits, dots, hyphens or underscores")
    return result


def quantity(value: Any, name: str = "Quantity", *, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= MAX_QTY:
        raise ValidationError(f"{name} must be an integer between {minimum} and {MAX_QTY}")
    return value


def utc_stamp(clock) -> str:
    stamp = clock()
    if not isinstance(stamp, datetime) or stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValidationError("Clock must provide an aware datetime")
    return stamp.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

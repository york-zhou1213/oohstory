"""Fixed-precision point accounting and deconstruction pricing helpers."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any


POINT_SCALE = 100
MAX_DOWNLOAD_POINTS = 999
MAX_DOWNLOAD_POINT_UNITS = MAX_DOWNLOAD_POINTS * POINT_SCALE
DECONSTRUCTION_CHARS_PER_POINT = 300_000


def point_units(value: Any, *, maximum: int = MAX_DOWNLOAD_POINT_UNITS) -> int:
    """Convert a public point value to integer hundredths without float math."""
    try:
        decimal_value = Decimal(str(value or 0))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("积分格式无效") from exc
    if not decimal_value.is_finite():
        raise ValueError("积分格式无效")
    units = int(
        (decimal_value * POINT_SCALE).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )
    if units < 0 or units > maximum:
        raise ValueError(f"下载积分必须在 0 至 {MAX_DOWNLOAD_POINTS} 之间")
    return units


def point_value(units: Any) -> int | float:
    """Return a JSON-safe point value with at most two decimal places."""
    normalized = int(units or 0)
    if normalized % POINT_SCALE == 0:
        return normalized // POINT_SCALE
    return float(Decimal(normalized) / POINT_SCALE)


def point_label(units: Any) -> str:
    """Render points without binary-float artifacts or unnecessary zeroes."""
    value = Decimal(int(units or 0)) / POINT_SCALE
    return format(value.quantize(Decimal("0.01")), "f").rstrip("0").rstrip(".") or "0"


def deconstruction_point_units(character_count: int) -> int:
    """Calculate the review reward for original text at 1 point per 300k chars."""
    characters = int(character_count)
    if characters < 0:
        raise ValueError("原文字数不能为负数")
    units = int(
        (Decimal(characters) * POINT_SCALE / DECONSTRUCTION_CHARS_PER_POINT).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )
    if units > MAX_DOWNLOAD_POINT_UNITS:
        raise ValueError(f"原文字数折算后超过 {MAX_DOWNLOAD_POINTS} 积分上限")
    return units


# Explicit semantic alias for new call sites. Keep the old name so already
# deployed workers and tests can roll forward without a flag day.
deconstruction_reward_units = deconstruction_point_units

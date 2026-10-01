"""Central validation for every value the integration writes to an inverter."""

from __future__ import annotations

import math
import re
from typing import Any, Final

MAX_CURRENT_A: Final = 300
MAX_POWER_W: Final = 30_000
MAX_PLANT_PRICE: Final = 10.0

_TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

_CURRENT_KEYS = frozenset(
    {
        "batteryMaxCurrentCharge",
        "batteryMaxCurrentDischarge",
        "chargeCurrent",
        "dischargeCurrent",
        "sdBatteryCurrent",
    }
)
_PERCENTAGE_KEYS = frozenset(
    {
        "batteryShutdownCap",
        "batteryRestartCap",
        "batteryLowCap",
        "generatorStartCap",
        "genOnCap",
        "genOffCap",
        *(f"cap{slot}" for slot in range(1, 7)),
    }
)
_POWER_KEYS = frozenset(
    {
        "zeroExportPower",
        "solarMaxSellPower",
        "pvMaxLimit",
        *(f"sellTime{slot}Pac" for slot in range(1, 7)),
    }
)
_ENUM_RANGES: dict[str, tuple[int, int]] = {
    "battMode": (0, 2),
    "sysWorkMode": (0, 4),
    "energyMode": (0, 1),
}
_BOOLEAN_KEYS = frozenset(
    {
        "solarSell",
        "batteryOn",
        "mondayOn",
        "tuesdayOn",
        "wednesdayOn",
        "thursdayOn",
        "fridayOn",
        "saturdayOn",
        "sundayOn",
        "genChargeOn",
        "gridAlwaysOn",
        "peakAndVallery",
        "sdChargeOn",
        "allowRemoteControl",
        *(f"time{slot}on" for slot in range(1, 7)),
        *(f"sellTime{slot}on" for slot in range(1, 7)),
        *(f"genTime{slot}on" for slot in range(1, 7)),
    }
)
_TIME_KEYS = frozenset(f"sellTime{slot}" for slot in range(1, 7))

WRITABLE_SETTING_KEYS: Final = frozenset().union(
    _CURRENT_KEYS,
    _PERCENTAGE_KEYS,
    _POWER_KEYS,
    _ENUM_RANGES,
    _BOOLEAN_KEYS,
    _TIME_KEYS,
)


class SunsynkSettingValidationError(ValueError):
    """Raised before an unsafe or unsupported setting reaches the API."""


def _integer_in_range(key: str, value: Any, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise SunsynkSettingValidationError(f"{key} must be an integer, not a boolean")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as err:
        raise SunsynkSettingValidationError(f"{key} must be an integer") from err
    if not math.isfinite(numeric) or not numeric.is_integer():
        raise SunsynkSettingValidationError(f"{key} must be a finite integer")
    result = int(numeric)
    if not minimum <= result <= maximum:
        raise SunsynkSettingValidationError(
            f"{key} must be between {minimum} and {maximum}; got {value!r}"
        )
    return result


def _boolean(key: str, value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return 1
        if lowered in {"0", "false", "no", "off"}:
            return 0
    if isinstance(value, (int, float)) and value in (0, 1):
        return int(value)
    raise SunsynkSettingValidationError(f"{key} must be a boolean or 0/1")


def validate_setting_value(key: str, value: Any) -> Any:
    """Validate and normalize one supported setting value."""
    if key in _CURRENT_KEYS:
        return _integer_in_range(key, value, 0, MAX_CURRENT_A)
    if key in _PERCENTAGE_KEYS:
        return _integer_in_range(key, value, 0, 100)
    if key in _POWER_KEYS:
        return _integer_in_range(key, value, 0, MAX_POWER_W)
    if key in _ENUM_RANGES:
        return _integer_in_range(key, value, *_ENUM_RANGES[key])
    if key in _BOOLEAN_KEYS:
        return _boolean(key, value)
    if key in _TIME_KEYS:
        if not isinstance(value, str) or _TIME_PATTERN.fullmatch(value) is None:
            raise SunsynkSettingValidationError(f"{key} must use 24-hour HH:MM format")
        return value
    raise SunsynkSettingValidationError(f"Unsupported writable setting: {key}")


def validate_setting_batch(settings: dict[str, Any]) -> dict[str, Any]:
    """Return a normalized copy, raising before the caller can enqueue the batch."""
    return {key: validate_setting_value(key, value) for key, value in settings.items()}


def validate_plant_price(value: Any) -> float:
    """Validate the constant plant energy price written to the plant API."""
    if isinstance(value, bool):
        raise SunsynkSettingValidationError("Plant price must be numeric")
    try:
        price = float(value)
    except (TypeError, ValueError) as err:
        raise SunsynkSettingValidationError("Plant price must be numeric") from err
    if not math.isfinite(price) or not 0 <= price <= MAX_PLANT_PRICE:
        raise SunsynkSettingValidationError(
            f"Plant price must be between 0 and {MAX_PLANT_PRICE}; got {value!r}"
        )
    return price

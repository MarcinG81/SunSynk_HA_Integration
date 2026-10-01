"""Diagnostics support for Sunsynk integration."""

from __future__ import annotations

import copy
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_EXPORT_PRICE_ENTITY,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_PRICE_ENTITY,
    CONF_SERIALS,
    DOMAIN,
)

_TO_REDACT = {
    "password",
    "access_token",
    "refresh_token",
    "username",
    CONF_SERIALS,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_PRICE_ENTITY,
    CONF_EXPORT_PRICE_ENTITY,
    "sn",
    "serial",
    "serialNo",
    "deviceSn",
    "email",
    "phone",
    "address",
    "alias",
    "name",
    "id",
}


def _safe_data(obj: Any) -> Any:
    """Recursively convert coordinator data to JSON-safe types."""
    if isinstance(obj, dict):
        return {k: _safe_data(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_safe_data(v) for v in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)


def _replace_sensitive_values(obj: Any, replacements: dict[str, str]) -> Any:
    """Replace sensitive identifiers wherever the API embeds them in strings."""
    if isinstance(obj, dict):
        return {
            key: _replace_sensitive_values(value, replacements)
            for key, value in obj.items()
        }
    if isinstance(obj, list):
        return [_replace_sensitive_values(value, replacements) for value in obj]
    if isinstance(obj, str):
        result = obj
        # Longest first prevents e.g. serial ``SN1`` from partially replacing
        # ``SN10`` before its own alias is considered.
        for sensitive, replacement in sorted(
            replacements.items(), key=lambda item: len(item[0]), reverse=True
        ):
            if sensitive:
                result = result.replace(sensitive, replacement)
        return result
    return obj


def _redacted_data(obj: Any, replacements: dict[str, str]) -> Any:
    """Make diagnostic data JSON-safe and redact keys plus embedded values."""
    redacted = async_redact_data(_safe_data(copy.deepcopy(obj)), _TO_REDACT)
    return _replace_sensitive_values(redacted, replacements)


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    from .coordinator import SolarForecastCoordinator, SunsynkCoordinator

    coordinator: SunsynkCoordinator = hass.data[DOMAIN][entry.entry_id]

    last_update_time = getattr(coordinator, "last_update_success_time", None)

    serials = list(getattr(coordinator, "serials", []) or [])
    raw_inverter_data = coordinator.data or {}
    for serial in raw_inverter_data:
        if serial not in serials:
            serials.append(serial)
    serial_aliases = {
        str(serial): f"inverter_{index}"
        for index, serial in enumerate(serials, start=1)
    }
    username = str(entry.data.get("username", ""))
    replacements = {**serial_aliases, username: "**REDACTED**"}

    redacted_inverter_data = {
        serial_aliases.get(str(serial), "inverter_unknown"): _redacted_data(
            payload, replacements
        )
        for serial, payload in raw_inverter_data.items()
    }

    result: dict[str, Any] = {
        "entry_data": _redacted_data(dict(entry.data), replacements),
        "entry_options": _redacted_data(dict(entry.options), replacements),
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "last_update_success_time": (
                last_update_time.isoformat() if last_update_time else None
            ),
        },
        "inverter_data": redacted_inverter_data,
    }

    forecast_coordinator: SolarForecastCoordinator | None = hass.data[DOMAIN].get(
        f"{entry.entry_id}_forecast"
    )
    if forecast_coordinator is not None:
        result["forecast_data"] = _redacted_data(
            forecast_coordinator.data, replacements
        )

    tariff_manager = hass.data[DOMAIN].get(f"{entry.entry_id}_tariff")
    if tariff_manager is not None:
        result["tariff"] = {
            "enabled": tariff_manager.is_enabled,
            "mode": tariff_manager.mode,
            "price_quality": tariff_manager.price_quality,
            "price_entity": "**REDACTED**",
            "export_price_quality": tariff_manager.export_price_quality,
            "export_price_entity": "**REDACTED**",
        }

    return result

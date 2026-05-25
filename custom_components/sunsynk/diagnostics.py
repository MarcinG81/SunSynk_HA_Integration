"""Diagnostics support for Sunsynk integration."""
from __future__ import annotations

import copy
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_TO_REDACT = {"password", "access_token", "refresh_token"}


def _safe_data(obj: Any) -> Any:
    """Recursively convert coordinator data to JSON-safe types."""
    if isinstance(obj, dict):
        return {k: _safe_data(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_safe_data(v) for v in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    from .coordinator import SolarForecastCoordinator, SunsynkCoordinator

    coordinator: SunsynkCoordinator = hass.data[DOMAIN][entry.entry_id]

    last_update_time = getattr(coordinator, "last_update_success_time", None)

    result: dict[str, Any] = {
        "entry_data": async_redact_data(dict(entry.data), _TO_REDACT),
        "entry_options": async_redact_data(dict(entry.options), _TO_REDACT),
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "last_update_success_time": (
                last_update_time.isoformat() if last_update_time else None
            ),
        },
        "inverter_data": _safe_data(copy.deepcopy(coordinator.data)),
    }

    forecast_coordinator: SolarForecastCoordinator | None = hass.data[DOMAIN].get(
        f"{entry.entry_id}_forecast"
    )
    if forecast_coordinator is not None:
        result["forecast_data"] = _safe_data(forecast_coordinator.data)

    tariff_manager = hass.data[DOMAIN].get(f"{entry.entry_id}_tariff")
    if tariff_manager is not None:
        result["tariff"] = {
            "enabled": tariff_manager.is_enabled,
            "mode": tariff_manager.mode,
            "price_quality": tariff_manager.price_quality,
            "price_entity": tariff_manager.price_entity,
        }

    return result

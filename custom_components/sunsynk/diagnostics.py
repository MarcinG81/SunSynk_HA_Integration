"""Diagnostics support for Sunsynk integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_TO_REDACT = {"password", "access_token", "refresh_token"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    from .coordinator import SolarForecastCoordinator, SunsynkCoordinator

    coordinator: SunsynkCoordinator = hass.data[DOMAIN][entry.entry_id]

    result: dict[str, Any] = {
        "entry_data": async_redact_data(dict(entry.data), _TO_REDACT),
        "entry_options": async_redact_data(dict(entry.options), _TO_REDACT),
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "last_update_success_time": (
                coordinator.last_update_success_time.isoformat()
                if coordinator.last_update_success_time
                else None
            ),
        },
        "inverter_data": coordinator.data,
    }

    forecast_coordinator: SolarForecastCoordinator | None = hass.data[DOMAIN].get(
        f"{entry.entry_id}_forecast"
    )
    if forecast_coordinator is not None:
        result["forecast_data"] = forecast_coordinator.data

    tariff_manager = hass.data[DOMAIN].get(f"{entry.entry_id}_tariff")
    if tariff_manager is not None:
        result["tariff"] = {
            "enabled": tariff_manager.is_enabled,
            "mode": tariff_manager.mode,
            "price_quality": tariff_manager.price_quality,
            "price_entity": tariff_manager.price_entity,
        }

    return result

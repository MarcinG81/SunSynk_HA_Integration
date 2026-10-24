"""Shared helpers for the Sunsynk integration."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DEYE_WRITABLE_KEYS, DOMAIN
from .coordinator import SunsynkCoordinator


def build_device_info(coordinator: SunsynkCoordinator, serial: str) -> DeviceInfo:
    """Build a full DeviceInfo for a given inverter serial."""
    from .const import _model_value

    inverter_data = (coordinator.data or {}).get(serial, {}).get("inverter", {})
    brand_raw = inverter_data.get("brand")
    brand = brand_raw if isinstance(brand_raw, str) and brand_raw else "Sunsynk"
    model = _model_value(inverter_data) or "Sunsynk Inverter"
    sw_version = (inverter_data.get("version") or {}).get("masterVer")

    return DeviceInfo(
        identifiers={(DOMAIN, serial)},
        name=inverter_data.get("alias") or f"Sunsynk {serial}",
        manufacturer=brand,
        model=model,
        sw_version=sw_version,
        serial_number=serial,
    )


def options_snapshot_key(entry_id: str) -> str:
    """hass.data key holding the options the running entry was set up with."""
    return f"{entry_id}_options"


def persist_runtime_option(
    hass: HomeAssistant, entry: ConfigEntry, key: str, value: Any
) -> None:
    """Save a value changed live (e.g. from an entity) so it survives restarts.

    The snapshot is updated first, so the options-update listener can tell
    this apart from a Configure change and skip reloading the integration.
    """
    if entry.options.get(key) == value:
        return
    options = {**entry.options, key: value}
    hass.data[DOMAIN][options_snapshot_key(entry.entry_id)] = options
    hass.config_entries.async_update_entry(entry, options=options)


def deyecloud_can_change(coordinator: Any, serial: str, setting_key: str) -> bool:
    """Whether Deye Cloud can change this setting and the inverter reports it."""
    settings = (coordinator.data or {}).get(serial, {}).get("settings", {}) or {}
    return setting_key in DEYE_WRITABLE_KEYS and setting_key in settings

"""Shared helpers for the Sunsynk integration."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN
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

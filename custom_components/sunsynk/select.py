"""Select entities for Sunsynk settings that take one of a few named values."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SunsynkCoordinator
from .helpers import build_device_info


@dataclass(frozen=True)
class SunsynkSelectEntityDescription(SelectEntityDescription):
    setting_key: str = ""
    # The inverter's value for each option, in the order shown.
    values: dict[str, int] = field(default_factory=dict)


# The three System Work Modes the Sunsynk portal offers. 3 and 4, once
# listed for set_work_mode, don't exist on Sunsynk / Deye inverters.
SYSTEM_WORK_MODES = {
    "selling_first": 0,
    "zero_export_limit_to_load": 1,
    "limited_to_home": 2,
}

SELECTS: tuple[SunsynkSelectEntityDescription, ...] = (
    SunsynkSelectEntityDescription(
        key="setting_sys_work_mode_select",
        translation_key="system_work_mode",
        setting_key="sysWorkMode",
        values=SYSTEM_WORK_MODES,
        options=list(SYSTEM_WORK_MODES),
        icon="mdi:solar-power-variant",
        entity_category=EntityCategory.CONFIG,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SunsynkCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        SunsynkSelectEntity(
            coordinator, serial, description, build_device_info(coordinator, serial)
        )
        for serial in coordinator.serials
        for description in SELECTS
    )


class SunsynkSelectEntity(CoordinatorEntity[SunsynkCoordinator], SelectEntity):
    """A setting with named options, written like any other setting."""

    entity_description: SunsynkSelectEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SunsynkCoordinator,
        serial: str,
        description: SunsynkSelectEntityDescription,
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._serial = serial
        self._attr_unique_id = f"{serial}_{description.key}"
        self._attr_device_info = device_info

    def _settings(self) -> dict[str, Any]:
        return (self.coordinator.data or {}).get(self._serial, {}).get("settings", {})

    @property
    def available(self) -> bool:
        """Unavailable when this inverter doesn't report the setting at all."""
        return (
            super().available
            and self.entity_description.setting_key in self._settings()
        )

    @property
    def current_option(self) -> str | None:
        """The option for the inverter's value; unknown for any other value."""
        try:
            value = int(self._settings().get(self.entity_description.setting_key))
        except (TypeError, ValueError):
            return None
        return next(
            (
                option
                for option, option_value in self.entity_description.values.items()
                if option_value == value
            ),
            None,
        )

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_write_setting(
            self._serial,
            self.entity_description.setting_key,
            self.entity_description.values[option],
        )

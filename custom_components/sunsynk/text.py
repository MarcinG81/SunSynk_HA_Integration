"""Text platform for writable time-string Sunsynk inverter settings."""
from __future__ import annotations

import re
from dataclasses import dataclass

from homeassistant.components.text import TextEntity, TextEntityDescription, TextMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SunsynkCoordinator
from .helpers import build_device_info

_TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


@dataclass(frozen=True)
class SunsynkTextEntityDescription(TextEntityDescription):
    setting_key: str = ""


WRITABLE_TEXTS: tuple[SunsynkTextEntityDescription, ...] = tuple(
    SunsynkTextEntityDescription(
        key=f"setting_sell_time{i}",
        name=f"Time Slot {i} Start",
        setting_key=f"sellTime{i}",
        pattern=r"^([01]\d|2[0-3]):[0-5]\d$",
        native_min=5,
        native_max=5,
        mode=TextMode.TEXT,
        entity_category=EntityCategory.CONFIG,
    )
    for i in range(1, 7)
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SunsynkCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SunsynkTextEntity] = []

    for serial in coordinator.serials:
        device_info = build_device_info(coordinator, serial)
        for description in WRITABLE_TEXTS:
            entities.append(SunsynkTextEntity(coordinator, serial, description, device_info))

    async_add_entities(entities)


class SunsynkTextEntity(CoordinatorEntity[SunsynkCoordinator], TextEntity):
    """A writable time string setting for a Sunsynk inverter."""

    entity_description: SunsynkTextEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SunsynkCoordinator,
        serial: str,
        description: SunsynkTextEntityDescription,
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._serial = serial
        self._attr_unique_id = f"{serial}_{description.key}"
        self._attr_device_info = device_info

    @property
    def native_value(self) -> str | None:
        settings = (self.coordinator.data or {}).get(self._serial, {}).get("settings", {})
        value = settings.get(self.entity_description.setting_key)
        if value is None:
            return None
        return str(value)

    async def async_set_value(self, value: str) -> None:
        if not _TIME_PATTERN.match(value):
            return
        await self.coordinator.async_write_setting(
            self._serial, self.entity_description.setting_key, value
        )

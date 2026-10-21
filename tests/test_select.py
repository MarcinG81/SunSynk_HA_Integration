"""System Work Mode as a select with the three modes Sunsynk offers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.sunsynk.select import (
    ENERGY_MODES,
    SELECTS,
    SYSTEM_WORK_MODES,
    SunsynkSelectEntity,
    async_setup_entry,
)

WORK_MODE = SELECTS[0]


def _entity(settings):
    coordinator = MagicMock()
    coordinator.last_update_success = True
    coordinator.data = {"INV1": {"settings": settings}}
    coordinator.async_write_setting = AsyncMock()
    return coordinator, SunsynkSelectEntity(coordinator, "INV1", WORK_MODE, MagicMock())


def test_the_three_sunsynk_modes_in_portal_order():
    assert WORK_MODE.options == [
        "selling_first",
        "zero_export_limit_to_load",
        "limited_to_home",
    ]
    assert list(SYSTEM_WORK_MODES.values()) == [0, 1, 2]
    assert WORK_MODE.translation_key == "system_work_mode"


@pytest.mark.parametrize(
    ("reported", "option"),
    [
        (0, "selling_first"),
        ("1", "zero_export_limit_to_load"),
        (2, "limited_to_home"),
        (3, None),  # not a Sunsynk mode
        ("bad", None),
        (None, None),
    ],
)
def test_current_option_follows_the_inverter(reported, option):
    _, entity = _entity({"sysWorkMode": reported})

    assert entity.current_option == option


def test_unavailable_when_the_inverter_has_no_work_mode():
    _, entity = _entity({})

    assert entity.available is False
    assert entity.unique_id == "INV1_setting_sys_work_mode_select"


@pytest.mark.asyncio
@pytest.mark.parametrize(("option", "value"), list(SYSTEM_WORK_MODES.items()))
async def test_choosing_a_mode_writes_its_value(option, value):
    coordinator, entity = _entity({"sysWorkMode": 0})

    await entity.async_select_option(option)

    coordinator.async_write_setting.assert_awaited_once_with(
        "INV1", "sysWorkMode", value
    )


@pytest.mark.asyncio
async def test_each_inverter_gets_every_select():
    coordinator = MagicMock(serials=["A", "B"], data={})
    hass = MagicMock(data={"sunsynk": {"entry": coordinator}})
    added = []

    await async_setup_entry(hass, MagicMock(entry_id="entry"), added.extend)

    assert [e.unique_id for e in added] == [
        "A_setting_sys_work_mode_select",
        "A_setting_energy_mode_select",
        "B_setting_sys_work_mode_select",
        "B_setting_energy_mode_select",
    ]


ENERGY_MODE = SELECTS[1]


def test_energy_mode_names_its_two_values():
    assert ENERGY_MODE.setting_key == "energyMode"
    assert ENERGY_MODE.options == ["battery_first", "load_first"]
    assert ENERGY_MODES == {"battery_first": 0, "load_first": 1}
    assert ENERGY_MODE.translation_key == "energy_mode"


@pytest.mark.asyncio
async def test_choosing_an_energy_mode_writes_its_value():
    coordinator = MagicMock()
    coordinator.last_update_success = True
    coordinator.data = {"INV1": {"settings": {"energyMode": "0"}}}
    coordinator.async_write_setting = AsyncMock()
    entity = SunsynkSelectEntity(coordinator, "INV1", ENERGY_MODE, MagicMock())
    assert entity.current_option == "battery_first"

    await entity.async_select_option("load_first")

    coordinator.async_write_setting.assert_awaited_once_with("INV1", "energyMode", 1)

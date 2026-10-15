"""Maximum export power: the system's grid export limit, single and parallel.

Each inverter in a parallel group applies its slot's sell power on its own,
so the system exports that times the number of inverters (#21: two 12 kW
inverters, an 11 kW permitted export). The configured total is shared
between the inverters, and only selling slots are capped: an idle or charge
slot's power is the battery's cap for the house.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.sunsynk.virtual_slots import (
    MODE_CHARGE,
    MODE_DISCHARGE,
    MODE_IDLE,
    Resolution,
    VirtualSlotScheduler,
)

MASTER = "MASTER"


def _scheduler(inverters: int, max_export_power, tariff_manager=None):
    slaves = [f"SLAVE{n}" for n in range(1, inverters)]
    coordinator = MagicMock()
    coordinator.serials = [*slaves, MASTER]
    coordinator.resolve_write_target.side_effect = lambda serial: MASTER
    coordinator.data = {
        MASTER: {"inverter": {"ratePower": 12000}, "settings": {}, "battery": {}},
    }
    coordinator.async_write_settings = AsyncMock()
    scheduler = VirtualSlotScheduler(
        MagicMock(),
        coordinator,
        "entry",
        MASTER,
        tariff_manager=tariff_manager,
        max_export_power=max_export_power,
    )
    return coordinator, scheduler


def _pac(coordinator) -> int:
    return coordinator.async_write_settings.call_args.args[1]["sellTime1Pac"]


@pytest.mark.parametrize(
    ("inverters", "limit", "share"),
    [
        (1, 11000, 11000),
        (2, 11000, 5500),  # #21
        (3, 10000, 3333),
        (1, None, None),
        (2, None, None),
    ],
)
def test_each_inverter_gets_its_share_of_the_limit(inverters, limit, share):
    _, scheduler = _scheduler(inverters, limit)

    assert scheduler._export_share() == share


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("inverters", "limit", "sell_power", "expected"),
    [
        (1, 11000, 8000, 8000),  # within the limit
        (1, 11000, 12000, 11000),
        (2, 11000, 5000, 5000),
        (2, 11000, 12000, 5500),  # 2 x 12 kW would be 24 kW
        (2, None, 12000, 12000),  # no limit set: as configured
    ],
)
async def test_selling_window_never_exceeds_the_export_share(
    inverters, limit, sell_power, expected
):
    coordinator, scheduler = _scheduler(inverters, limit)

    await scheduler._write_window_if_changed(
        1, Resolution(MODE_DISCHARGE, None, 20, sell_power, "virtual_slot:1"), "16:00"
    )

    assert _pac(coordinator) == expected
    if limit:
        assert expected * inverters <= limit


@pytest.mark.parametrize(("inverters", "expected"), [(1, 11000), (2, 5500)])
def test_tariff_discharge_override_sells_at_the_share(inverters, expected):
    tariff_manager = MagicMock(discharge_min_soc=10)
    tariff_manager.is_charging_active_for.return_value = False
    tariff_manager.is_discharging_active_for.return_value = True
    _, scheduler = _scheduler(inverters, 11000, tariff_manager)

    assert scheduler._resolve_override().sell_power == expected


def test_tariff_discharge_override_without_a_limit_uses_rated_power():
    tariff_manager = MagicMock(discharge_min_soc=10)
    tariff_manager.is_charging_active_for.return_value = False
    tariff_manager.is_discharging_active_for.return_value = True
    _, scheduler = _scheduler(2, None, tariff_manager)

    assert scheduler._resolve_override().sell_power == 12000


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [MODE_IDLE, MODE_CHARGE])
async def test_non_selling_slots_keep_the_house_on_battery(mode):
    """Capping these would push the house onto the grid; they export nothing."""
    coordinator, scheduler = _scheduler(2, 11000)

    await scheduler._write_window_if_changed(
        1, Resolution(mode, None, 90, 0, "virtual_slot:1"), "16:00"
    )

    assert _pac(coordinator) == 12000

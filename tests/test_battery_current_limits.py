"""Charge/discharge currents against the battery, single and parallel (#21).

One inverter draws the current it is set to. In a parallel group every
inverter applies the same per-inverter current to one shared bank, so the
bank supplies that current times the number of inverters. Whatever Tariff
Manager or the Virtual Slot Scheduler writes must keep that total within
what the BMS allows; above it, each inverter gets the BMS limit divided by
the number of inverters.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.sunsynk.current_limits import current_limit, group_sizes
from custom_components.sunsynk.tariff import TariffChargingManager
from custom_components.sunsynk.virtual_slots import (
    MODE_CHARGE,
    MODE_DISCHARGE,
    Resolution,
    VirtualSlotScheduler,
)

MASTER = "MASTER"


def _coordinator(inverters: int, battery: dict) -> MagicMock:
    """A master plus `inverters - 1` slaves sharing one battery bank.

    Only the master reports the BMS: the slaves' battery data is empty, as
    in the #21 diagnostics.
    """
    slaves = [f"SLAVE{n}" for n in range(1, inverters)]
    coordinator = MagicMock()
    coordinator.serials = [*slaves, MASTER]
    coordinator.write_target_serials = [MASTER]
    coordinator.resolve_write_target.side_effect = lambda serial: MASTER
    coordinator.data = {
        MASTER: {"battery": {"soc": 50, **battery}, "settings": {}},
        **{slave: {"battery": {}, "settings": {}} for slave in slaves},
    }
    coordinator.async_write_setting = AsyncMock()
    coordinator.async_write_settings = AsyncMock()
    return coordinator


def _manager(coordinator, *, charge=100, discharge=100, normal=50):
    hass = MagicMock()
    hass.async_create_task.side_effect = lambda coroutine: coroutine.close()
    manager = TariffChargingManager(
        hass=hass,
        coordinator=coordinator,
        price_entity="sensor.price",
        cheap_threshold=0.10,
        cheap_current=charge,
        normal_charge_current=normal,
        target_soc=90,
        expensive_threshold=0.30,
        peak_discharge_current=discharge,
        normal_discharge_current=normal,
        discharge_min_soc=10,
        start_hour=None,
        end_hour=None,
    )
    manager._enabled = True
    return manager


def _written(coordinator, key: str) -> list[int]:
    return [
        call.args[2]
        for call in coordinator.async_write_setting.call_args_list
        if call.args[1] == key
    ]


# (inverters, BMS limit, configured per inverter, expected per inverter)
_CASES = [
    pytest.param(1, 200, 150, 150, id="single-within-bms"),
    pytest.param(1, 200, 250, 200, id="single-above-bms"),
    pytest.param(1, 200, 200, 200, id="single-exactly-bms"),
    pytest.param(2, 1040, 240, 240, id="parallel-2x240-within-1040"),
    pytest.param(2, 400, 240, 200, id="parallel-2x240-above-400"),
    pytest.param(2, 192, 120, 96, id="parallel-2x120-above-192"),
    pytest.param(2, 241, 240, 120, id="parallel-odd-limit-rounds-down"),
    pytest.param(3, 600, 240, 200, id="parallel-3x240-above-600"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("inverters", "bms", "configured", "expected"), _CASES)
async def test_tariff_charge_current_respects_the_battery(
    inverters, bms, configured, expected
):
    coordinator = _coordinator(inverters, {"chargeCurrentLimit": bms})
    manager = _manager(coordinator, charge=configured)

    await manager._evaluate_charging(MASTER, 0.05, 50, True)

    assert _written(coordinator, "chargeCurrent") == [expected]
    assert expected * inverters <= bms
    # The notification shows what was actually set.
    message = manager._hass.services.async_call.call_args.args[2]["message"]
    assert f"Charging at {expected} A" in message


@pytest.mark.asyncio
@pytest.mark.parametrize(("inverters", "bms", "configured", "expected"), _CASES)
async def test_tariff_discharge_current_respects_the_battery(
    inverters, bms, configured, expected
):
    coordinator = _coordinator(inverters, {"dischargeCurrentLimit": bms})
    manager = _manager(coordinator, discharge=configured)

    await manager._evaluate_discharging(MASTER, 0.40, 50, True)

    assert _written(coordinator, "dischargeCurrent") == [expected]
    assert expected * inverters <= bms


@pytest.mark.asyncio
async def test_rated_bms_limit_is_used_before_the_live_one():
    coordinator = _coordinator(
        2, {"maxDischargeCurrentLimit": 300, "dischargeCurrentLimit": 1040}
    )
    manager = _manager(coordinator, discharge=240)

    await manager._evaluate_discharging(MASTER, 0.40, 50, True)

    assert _written(coordinator, "dischargeCurrent") == [150]


@pytest.mark.asyncio
@pytest.mark.parametrize("inverters", [1, 2])
async def test_no_bms_limit_leaves_the_current_as_configured(inverters):
    """A battery without BMS communication reports no limit (0 or nothing)."""
    coordinator = _coordinator(
        inverters, {"chargeCurrentLimit": 0, "maxChargeCurrentLimit": 0}
    )
    manager = _manager(coordinator, charge=240)

    await manager._evaluate_charging(MASTER, 0.05, 50, True)

    assert _written(coordinator, "chargeCurrent") == [240]


@pytest.mark.asyncio
async def test_restored_normal_current_also_respects_the_battery():
    coordinator = _coordinator(2, {"chargeCurrentLimit": 192})
    manager = _manager(coordinator, charge=80, normal=150)
    manager._charging_active_serials.add(MASTER)

    await manager._stop_charging(MASTER, "test")

    assert _written(coordinator, "chargeCurrent") == [96]


@pytest.mark.asyncio
async def test_bms_limit_changes_mid_charge_do_not_rewrite_the_current():
    """The BMS limit moves a few amps at a time; each change must not become
    another write to the master. It is checked when a current is written."""
    coordinator = _coordinator(2, {"chargeCurrentLimit": 192})
    manager = _manager(coordinator, charge=120)
    await manager._evaluate_charging(MASTER, 0.05, 50, True)

    coordinator.data[MASTER]["battery"]["chargeCurrentLimit"] = 189
    await manager._evaluate_charging(MASTER, 0.05, 51, True)

    assert _written(coordinator, "chargeCurrent") == [96]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "key", "battery_key"),
    [
        (MODE_CHARGE, "chargeCurrent", "chargeCurrentLimit"),
        (MODE_DISCHARGE, "dischargeCurrent", "dischargeCurrentLimit"),
    ],
)
@pytest.mark.parametrize(("inverters", "bms", "configured", "expected"), _CASES)
async def test_virtual_slot_current_respects_the_battery(
    mode, key, battery_key, inverters, bms, configured, expected
):
    coordinator = _coordinator(inverters, {battery_key: bms})
    scheduler = VirtualSlotScheduler(MagicMock(), coordinator, "entry", MASTER)

    await scheduler._apply_current_if_changed(
        Resolution(mode, configured, 90, 0, "virtual_slot:1")
    )

    coordinator.async_write_setting.assert_awaited_once_with(MASTER, key, expected)


@pytest.mark.parametrize(
    ("inverters", "rated", "configured", "allowed"),
    [
        (1, 200, 200, True),
        (1, 200, 201, False),
        (2, 400, 200, True),
        (2, 400, 240, False),  # 2 x 240 = 480 A > 400 A
        (2, 1040, 240, True),  # #21: 480 A of a 1040 A bank
    ],
)
def test_configure_check_shares_the_rated_bms_limit(
    inverters, rated, configured, allowed
):
    """Configure refuses a current only on fixed limits, never live ones."""
    coordinator = _coordinator(
        inverters, {"maxDischargeCurrentLimit": rated, "dischargeCurrentLimit": 1}
    )

    limit = current_limit(
        coordinator.data, [MASTER], "discharge", group_sizes(coordinator)
    )

    assert (configured <= limit.maximum) is allowed

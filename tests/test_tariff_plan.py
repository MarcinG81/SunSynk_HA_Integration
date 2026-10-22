"""Tariff Manager charging / discharging in the planned slots (#40)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from custom_components.sunsynk.tariff import TariffChargingManager
from tests.test_tariff import _price_state

SERIAL = "TEST123"
BST = timezone(timedelta(hours=1))
# Agile Outgoing 2026-10-21 16:00-20:00 (#40).
_EVENING = [0.2487, 0.2546, 0.2566, 0.2607, 0.2661, 0.2659, 0.1862, 0.1863]


def _rates(day=21, prices=_EVENING, first_hour=16):
    start = datetime(2026, 10, day, first_hour, 0, tzinfo=BST)
    return SimpleNamespace(
        attributes={
            "rates": [
                {
                    "start": (start + timedelta(minutes=30 * n)).isoformat(),
                    "end": (start + timedelta(minutes=30 * (n + 1))).isoformat(),
                    "value_inc_vat": price,
                }
                for n, price in enumerate(prices)
            ]
        }
    )


def _manager(hass, coordinator, *, sell_kwh=5, buy_kwh=None, expensive=0.15):
    manager = TariffChargingManager(
        hass=hass,
        coordinator=coordinator,
        price_entity="sensor.price",
        cheap_threshold=0.10,
        cheap_current=100,
        normal_charge_current=50,
        target_soc=90,
        expensive_threshold=expensive,
        peak_discharge_current=100,  # 100 A × 50 V = 5 kW
        normal_discharge_current=50,
        discharge_min_soc=10,
        plan_export_entities=["event.export_today", "event.export_tomorrow"],
        plan_sell_kwh=sell_kwh,
        plan_import_entities=["event.import_today"] if buy_kwh else None,
        plan_buy_kwh=buy_kwh,
    )
    manager._enabled = True
    return manager


@pytest.fixture
def states(mock_hass):
    entities = {
        "sensor.price": _price_state("0.20"),
        "event.export_today": _rates(),
    }
    mock_hass.states.get.side_effect = entities.get
    return entities


@pytest.fixture
def coordinator(mock_coordinator):
    mock_coordinator.data[SERIAL]["battery"].update({"soc": 80, "voltage": 50})
    return mock_coordinator


def _at(hour, minute=0, day=21):
    return patch(
        "custom_components.sunsynk.tariff.dt_util.utcnow",
        return_value=datetime(2026, 10, day, hour, minute, tzinfo=BST).astimezone(
            timezone.utc
        ),
    )


@pytest.mark.asyncio
async def test_discharges_only_in_the_dearest_slots(mock_hass, coordinator, states):
    """5 kWh at 5 kW: the two dearest half hours, 18:00-19:00."""
    manager = _manager(mock_hass, coordinator)

    with _at(12):
        await manager._evaluate()
        assert [slot["price"] for slot in manager.plans["discharge"]] == [
            0.2661,
            0.2659,
        ]
    assert not manager.is_discharging_active_for(SERIAL)
    reason = manager.decisions[SERIAL]["discharging"]
    assert reason.startswith("not a planned slot; next at ")
    assert reason.endswith("(0.2661)")

    with _at(18, 10):
        await manager._evaluate()
    assert manager.is_discharging_active_for(SERIAL)

    with _at(19, 5):
        await manager._evaluate()
    assert not manager.is_discharging_active_for(SERIAL)


@pytest.mark.asyncio
async def test_a_day_is_planned_once(mock_hass, coordinator, states):
    """After a planned slot has passed, no other slot takes its place:
    that would sell more than the day's amount."""
    manager = _manager(mock_hass, coordinator)
    with _at(12):
        await manager._evaluate()

    with _at(18, 45):
        await manager._evaluate()
        assert [slot["price"] for slot in manager.plans["discharge"]] == [0.2659]


@pytest.mark.asyncio
async def test_new_prices_for_a_day_replan_it(mock_hass, coordinator, states):
    manager = _manager(mock_hass, coordinator)
    with _at(12):
        await manager._evaluate()

    states["event.export_today"] = _rates(prices=[0.30] + _EVENING[1:])
    with _at(12, 30):
        await manager._evaluate()
        assert [slot["price"] for slot in manager.plans["discharge"]] == [
            0.30,
            0.2661,
        ]


@pytest.mark.asyncio
async def test_the_next_day_is_planned_when_its_prices_arrive(
    mock_hass, coordinator, states
):
    manager = _manager(mock_hass, coordinator)
    with _at(12):
        await manager._evaluate()

    states["event.export_tomorrow"] = _rates(day=22)
    with _at(16, 5):
        await manager._evaluate()
        days = {slot["start"][:10] for slot in manager.plans["discharge"]}
    assert days == {"2026-10-21", "2026-10-22"}

    # Past days are dropped.
    with _at(1, day=23):
        await manager._evaluate()
    assert manager._plans["discharge"] == {}


@pytest.mark.asyncio
async def test_the_threshold_caps_the_plan(mock_hass, coordinator, states):
    manager = _manager(mock_hass, coordinator, sell_kwh=50, expensive=0.26)

    with _at(12):
        await manager._evaluate()
        assert [slot["price"] for slot in manager.plans["discharge"]] == [
            0.2607,
            0.2661,
            0.2659,
        ]


@pytest.mark.asyncio
async def test_charging_in_the_cheapest_slots(mock_hass, coordinator, states):
    states["event.import_today"] = _rates(prices=[0.30, 0.05, 0.30, 0.30])
    manager = _manager(mock_hass, coordinator, buy_kwh=2.5)

    with _at(16, 40):
        await manager._evaluate()
        assert [slot["price"] for slot in manager.plans["charge"]] == [0.05]
    assert manager.is_charging_active_for(SERIAL)


@pytest.mark.asyncio
async def test_no_prices_yet_means_nothing_planned(mock_hass, coordinator, states):
    del states["event.export_today"]
    manager = _manager(mock_hass, coordinator)

    with _at(18, 10):
        await manager._evaluate()

    assert not manager.is_discharging_active_for(SERIAL)
    assert manager.decisions[SERIAL]["discharging"] == (
        "no planned slot ahead (prices not in yet?)"
    )


@pytest.mark.asyncio
async def test_without_a_battery_voltage_51_2_v_is_assumed(mock_hass, coordinator, states):
    coordinator.data[SERIAL]["battery"]["voltage"] = None
    manager = _manager(mock_hass, coordinator)

    assert manager._plan_power_kw("discharge", [SERIAL]) == pytest.approx(5.12)


def test_the_plan_is_off_without_entities_or_kwh(mock_hass, coordinator):
    manager = _manager(mock_hass, coordinator, sell_kwh=None)

    assert manager.plans == {}


def test_the_tariff_mode_sensor_shows_the_plan(mock_hass, coordinator, states):
    from custom_components.sunsynk.sensor import TariffStateSensor

    manager = _manager(mock_hass, coordinator)
    sensor = TariffStateSensor("entry", manager, MagicMock())
    assert sensor.extra_state_attributes["planned_slots"] == {"discharge": []}


@pytest.mark.asyncio
async def test_outside_the_planned_charge_slots_it_says_when_the_next_is(
    mock_hass, coordinator, states
):
    states["event.import_today"] = _rates(prices=[0.30, 0.05, 0.30, 0.30])
    manager = _manager(mock_hass, coordinator, buy_kwh=2.5)

    with _at(12):
        await manager._evaluate()

    assert not manager.is_charging_active_for(SERIAL)
    assert manager.decisions[SERIAL]["charging"].endswith("(0.05)")

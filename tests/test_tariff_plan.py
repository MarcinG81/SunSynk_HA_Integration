"""Tariff Manager charging / discharging in the planned slots (#40)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

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


def _manager(
    hass, coordinator, *, sell_kwh=5, buy_kwh=None, expensive=0.15, entry_id=""
):
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
        entry_id=entry_id,
    )
    manager._enabled = True
    # A setter's re-evaluation is run by each test itself.
    hass.async_create_task.side_effect = lambda coro: coro.close()
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


# ── #42: whole-system amounts, real power, a day under way, restarts, pause ──


def _pair(coordinator):
    """A parallel pair: SLAVE's writes go to SERIAL."""
    coordinator.serials = [SERIAL, "SLAVE"]
    coordinator.write_target_serials = [SERIAL]
    coordinator.resolve_write_target.side_effect = lambda serial: SERIAL
    coordinator.data["SLAVE"] = {"battery": {"voltage": 50}}
    return coordinator


@pytest.mark.asyncio
async def test_the_amount_is_for_the_whole_parallel_group(
    mock_hass, coordinator, states
):
    """10 kWh for a pair at 5 kW each is one hour: the two dearest slots."""
    manager = _manager(mock_hass, _pair(coordinator), sell_kwh=10)

    with _at(12):
        await manager._evaluate()
        assert [slot["price"] for slot in manager.plans["discharge"]] == [
            0.2661,
            0.2659,
        ]
    assert manager.plan_power == {"discharge": 10.0}


def test_the_power_stays_within_the_rated_power_and_export_share(
    mock_hass, coordinator
):
    manager = _manager(mock_hass, coordinator)
    coordinator.data[SERIAL]["inverter"] = {"ratePower": 3000}
    assert manager._plan_power_kw("discharge", [SERIAL]) == pytest.approx(3.0)

    manager._max_export_power = 2000
    assert manager._plan_power_kw("discharge", [SERIAL]) == pytest.approx(2.0)
    # The export limit doesn't apply to buying.
    manager._cheap_current = 100
    assert manager._plan_power_kw("charge", [SERIAL]) == pytest.approx(3.0)


def test_the_plan_uses_the_slot_power_not_the_current(mock_hass, coordinator):
    """#42: a pair of 12 kW inverters keeps to the slot power, 6 kW each
    within a 12 kW export limit, whatever the 27 A current."""
    _pair(coordinator)
    coordinator.data[SERIAL]["inverter"] = {"ratePower": 12000}
    coordinator.data[SERIAL]["battery"]["voltage"] = 196.7
    manager = _manager(mock_hass, coordinator, sell_kwh=10)
    manager._peak_discharge_current = 27
    manager._cheap_current = 5
    manager._max_export_power = 12000

    assert manager._plan_power_kw("discharge", [SERIAL]) == pytest.approx(12.0)
    assert manager._plan_power_kw("charge", [SERIAL]) == pytest.approx(24.0)


@pytest.mark.asyncio
async def test_a_day_under_way_gets_its_share_of_the_amount(
    mock_hass, coordinator, states
):
    """Planned at 18:00, half of the 16:00-20:00 prices are ahead: 5 of the
    10 kWh, so two slots rather than all four left."""
    manager = _manager(mock_hass, coordinator, sell_kwh=10)

    with _at(18):
        await manager._evaluate()
        assert [slot["price"] for slot in manager.plans["discharge"]] == [
            0.2661,
            0.2659,
        ]


@pytest.mark.asyncio
async def test_a_new_amount_replans_the_day(mock_hass, coordinator, states):
    manager = _manager(mock_hass, coordinator)
    assert manager.plan_sell_kwh == 5
    assert manager.plan_buy_kwh is None
    with _at(12):
        await manager._evaluate()

    manager.set_plan_sell_kwh(2.5)
    manager.set_plan_buy_kwh(None)
    with _at(12, 5):
        await manager._evaluate()
        assert [slot["price"] for slot in manager.plans["discharge"]] == [0.2661]

    manager.set_plan_sell_kwh(0)
    assert manager.plans == {}


@pytest.mark.asyncio
async def test_the_plan_can_be_paused(mock_hass, coordinator, states):
    manager = _manager(mock_hass, coordinator)
    assert manager.plans_configured("discharge")
    assert not manager.plans_configured("charge")

    manager.set_plan_paused(True)
    with _at(18, 10):
        await manager._evaluate()
    assert manager.plan_paused
    assert not manager.is_discharging_active_for(SERIAL)
    assert manager.decisions[SERIAL]["discharging"] == "the price plan is paused"

    manager.set_plan_paused(False)
    with _at(18, 15):
        await manager._evaluate()
    assert manager.is_discharging_active_for(SERIAL)


@pytest.mark.asyncio
async def test_plans_are_kept_over_a_restart(mock_hass, coordinator, states):
    """After a restart at 18:45, the 18:00 slot that has passed isn't
    replaced by another one: the day is still planned once."""
    store = MagicMock()
    store.async_load = AsyncMock(return_value=None)
    with patch("custom_components.sunsynk.tariff.Store", return_value=store):
        manager = _manager(mock_hass, coordinator, entry_id="entry")
        await manager.async_load()
        with _at(12):
            await manager._evaluate()
    saved = store.async_delay_save.call_args.args[0]()
    assert list(saved["discharge"]) == ["2026-10-21"]
    saved["discharge"]["2026-10-20"] = saved["discharge"]["2026-10-21"]
    saved["discharge"]["broken"] = {"kwh": 5}

    store.async_load = AsyncMock(return_value=saved)
    with patch("custom_components.sunsynk.tariff.Store", return_value=store):
        restarted = _manager(mock_hass, coordinator, entry_id="entry")
        with _at(18, 45):
            await restarted.async_load()
            await restarted._evaluate()
            assert [slot["price"] for slot in restarted.plans["discharge"]] == [
                0.2659
            ]


def test_the_tariff_mode_sensor_shows_the_plan_power_and_pause(
    mock_hass, coordinator, states
):
    from custom_components.sunsynk.sensor import TariffStateSensor

    manager = _manager(mock_hass, coordinator)
    manager._plan_power = {"discharge": 5.0}
    manager._plans["discharge"][datetime(2026, 10, 21).date()] = (
        (),
        parse_rates_for_test(),
    )
    sensor = TariffStateSensor("entry", manager, MagicMock())
    with _at(12):
        attrs = sensor.extra_state_attributes
    assert attrs["planned_power_kw"] == {"discharge": 5.0}
    assert attrs["plan_paused"] is False


def parse_rates_for_test():
    from custom_components.sunsynk.price_plan import parse_rates

    return parse_rates(_rates().attributes)[:1]


@pytest.mark.asyncio
async def test_the_price_plan_switch_pauses_and_persists():
    from tests.test_tariff_persistence import _hass_and_entry

    from custom_components.sunsynk.switch import TariffPlanSwitch

    hass, entry = _hass_and_entry()
    manager = MagicMock(plan_paused=False)
    switch = TariffPlanSwitch("entry", manager, MagicMock(), entry)
    switch.hass = hass
    assert switch.is_on
    assert switch.unique_id == "entry_tariff_plan"

    await switch.async_turn_off()
    assert entry.options == {"plan_paused": True}
    await switch.async_turn_on()
    assert entry.options == {"plan_paused": False}
    assert [c.args for c in manager.set_plan_paused.call_args_list] == [
        (True,),
        (False,),
    ]

    switch.async_write_ha_state = MagicMock()
    await switch.async_added_to_hass()
    listener = manager.async_add_listener.call_args.args[0]
    listener()
    switch.async_write_ha_state.assert_called_once()
    await switch.async_will_remove_from_hass()
    manager.async_add_listener.return_value.assert_called_once()
    await switch.async_will_remove_from_hass()

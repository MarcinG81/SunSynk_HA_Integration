"""Tests for TariffChargingManager state machine."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.sunsynk.api.client import SunsynkApiError

from custom_components.sunsynk.tariff import (
    QUALITY_INVALID,
    QUALITY_NOT_FOUND,
    QUALITY_OK,
    QUALITY_STALE,
    QUALITY_UNAVAILABLE,
    TariffChargingManager,
)


def _make_manager(
    hass,
    coordinator,
    *,
    cheap_threshold=0.10,
    cheap_current=100,
    normal_charge=50,
    target_soc=90,
    expensive_threshold=0.30,
    peak_discharge=100,
    normal_discharge=50,
    discharge_min_soc=10,
    start_hour=None,
    end_hour=None,
    price_max_age=90,
    export_price_entity=None,
) -> TariffChargingManager:
    return TariffChargingManager(
        hass=hass,
        coordinator=coordinator,
        price_entity="sensor.electricity_price",
        cheap_threshold=cheap_threshold,
        cheap_current=cheap_current,
        normal_charge_current=normal_charge,
        target_soc=target_soc,
        expensive_threshold=expensive_threshold,
        peak_discharge_current=peak_discharge,
        normal_discharge_current=normal_discharge,
        discharge_min_soc=discharge_min_soc,
        start_hour=start_hour,
        end_hour=end_hour,
        price_max_age_minutes=price_max_age,
        export_price_entity=export_price_entity,
    )


def _price_state(
    value: str, age_seconds: int = 60, reported_seconds: int | None = None
) -> MagicMock:
    state = MagicMock()
    state.state = value
    now = datetime.now(tz=timezone.utc)
    state.last_updated = now - timedelta(seconds=age_seconds)
    state.last_reported = now - timedelta(
        seconds=age_seconds if reported_seconds is None else reported_seconds
    )
    return state


def _consume_created_tasks(hass):
    def _consume(coro):
        coro.close()
        return MagicMock()

    hass.async_create_task.side_effect = _consume


# ── Mode property ────────────────────────────────────────────────────────────


def test_mode_disabled_by_default(mock_hass, mock_coordinator):
    mgr = _make_manager(mock_hass, mock_coordinator)
    assert mgr.mode == "disabled"
    assert not mgr.is_enabled


def test_mode_idle_when_enabled(mock_hass, mock_coordinator):
    mock_hass.states.get.return_value = _price_state("0.20")
    mgr = _make_manager(mock_hass, mock_coordinator)
    mgr._enabled = True
    assert mgr.mode == "idle"


def test_mode_charging(mock_hass, mock_coordinator):
    mgr = _make_manager(mock_hass, mock_coordinator)
    mgr._enabled = True
    mgr._charging_active_serials.add("TEST123")
    assert mgr.mode == "charging"


def test_mode_discharging(mock_hass, mock_coordinator):
    mgr = _make_manager(mock_hass, mock_coordinator)
    mgr._enabled = True
    mgr._discharging_active_serials.add("TEST123")
    assert mgr.mode == "discharging"


# ── Price quality ────────────────────────────────────────────────────────────


def test_quality_not_found_when_entity_missing(mock_hass, mock_coordinator):
    mock_hass.states.get.return_value = None
    mgr = _make_manager(mock_hass, mock_coordinator)
    quality, _ = mgr._compute_price_quality()
    assert quality == QUALITY_NOT_FOUND


def test_quality_unavailable(mock_hass, mock_coordinator):
    state = MagicMock()
    state.state = "unavailable"
    mock_hass.states.get.return_value = state
    mgr = _make_manager(mock_hass, mock_coordinator)
    quality, _ = mgr._compute_price_quality()
    assert quality == QUALITY_UNAVAILABLE


def test_quality_invalid_non_numeric(mock_hass, mock_coordinator):
    mock_hass.states.get.return_value = _price_state("not_a_number")
    mgr = _make_manager(mock_hass, mock_coordinator)
    quality, _ = mgr._compute_price_quality()
    assert quality == QUALITY_INVALID


def test_quality_ok(mock_hass, mock_coordinator):
    mock_hass.states.get.return_value = _price_state("0.15", age_seconds=60)
    mgr = _make_manager(mock_hass, mock_coordinator, price_max_age=90)
    quality, _ = mgr._compute_price_quality()
    assert quality == QUALITY_OK


def test_quality_stale(mock_hass, mock_coordinator):
    mock_hass.states.get.return_value = _price_state("0.15", age_seconds=6000)
    mgr = _make_manager(mock_hass, mock_coordinator, price_max_age=90)
    quality, _ = mgr._compute_price_quality()
    assert quality == QUALITY_STALE


def test_start_stop_and_callbacks(mock_hass, mock_coordinator):
    mock_hass.states.get.return_value = None
    _consume_created_tasks(mock_hass)
    unsubscribe_price = MagicMock()
    unsubscribe_coordinator = MagicMock()
    mock_coordinator.async_add_listener.return_value = unsubscribe_coordinator
    mgr = _make_manager(
        mock_hass,
        mock_coordinator,
        export_price_entity="sensor.export",
    )
    with patch(
        "custom_components.sunsynk.tariff.async_track_state_change_event",
        return_value=unsubscribe_price,
    ):
        mgr.start()
    mgr._on_price_changed(None)
    mgr._on_coordinator_update()
    mgr.stop()
    unsubscribe_price.assert_called_once()
    unsubscribe_coordinator.assert_called_once()


def test_listener_unsubscribe_and_all_runtime_properties(mock_hass, mock_coordinator):
    _consume_created_tasks(mock_hass)
    mgr = _make_manager(mock_hass, mock_coordinator)
    assert mgr.mode_for("TEST123") == "disabled"
    callback = MagicMock()
    unsubscribe = mgr.async_add_listener(callback)

    mgr.set_enabled(True)
    mgr.set_cheap_threshold(0.11)
    mgr.set_cheap_current(91)
    mgr.set_normal_charge_current(41)
    mgr.set_target_soc(88)
    mgr.set_expensive_threshold(0.31)
    mgr.set_peak_discharge_current(92)
    mgr.set_normal_discharge_current(42)
    mgr.set_discharge_min_soc(12)

    assert mgr.is_enabled
    assert not mgr.is_charging_active
    assert not mgr.is_discharging_active
    assert not mgr.is_charging_active_for("TEST123")
    assert not mgr.is_discharging_active_for("TEST123")
    assert mgr.mode_for("TEST123") == "idle"
    assert mgr.per_inverter_modes == {"TEST123": "idle"}
    assert mgr.soc_quality_by_inverter == {}
    assert mgr.price_quality == QUALITY_NOT_FOUND
    assert mgr.export_price_quality == QUALITY_NOT_FOUND
    assert mgr.price_entity == "sensor.electricity_price"
    assert mgr.export_price_entity == "sensor.electricity_price"
    assert mgr.cheap_threshold == 0.11
    assert mgr.expensive_threshold == 0.31
    assert mgr.target_soc == 88
    assert mgr.normal_charge_current == 41
    assert mgr.normal_discharge_current == 42
    assert mgr.discharge_min_soc == 12
    assert mgr.start_hour is None
    assert mgr.end_hour is None
    assert mgr.price_max_age_minutes == 90
    unsubscribe()
    assert callback not in mgr._listeners


def test_read_price_invalid_and_disabled_re_evaluate(mock_hass, mock_coordinator):
    mgr = _make_manager(mock_hass, mock_coordinator)
    assert mgr._read_price("sensor.price") is None
    mock_hass.states.get.return_value = _price_state("bad")
    assert mgr._read_price("sensor.price") is None
    mgr._re_evaluate()
    mock_hass.async_create_task.assert_not_called()


@pytest.mark.asyncio
async def test_disabled_evaluate_is_noop(mock_hass, mock_coordinator):
    mgr = _make_manager(mock_hass, mock_coordinator)
    await mgr._evaluate_locked()
    mock_coordinator.async_write_setting.assert_not_awaited()


@pytest.mark.asyncio
async def test_export_quality_failure_stops_discharge(mock_hass, mock_coordinator):
    mgr = _make_manager(
        mock_hass, mock_coordinator, export_price_entity="sensor.export"
    )
    mgr._enabled = True
    mgr._discharging_active_serials.add("TEST123")
    mock_hass.states.get.side_effect = lambda entity: (
        _price_state("0.2") if entity == "sensor.electricity_price" else None
    )
    await mgr._evaluate_locked()
    assert "TEST123" not in mgr._discharging_active_serials


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("in_schedule", "price", "soc"),
    [(False, 0.5, 50), (True, 0.5, 10)],
)
async def test_discharge_stop_reason_branches(
    mock_hass, mock_coordinator, in_schedule, price, soc
):
    mgr = _make_manager(mock_hass, mock_coordinator)
    mgr._discharging_active_serials.add("TEST123")
    await mgr._evaluate_discharging("TEST123", price, soc, in_schedule)
    assert "TEST123" not in mgr._discharging_active_serials


def test_mode_is_idle_with_no_write_targets(mock_hass, mock_coordinator):
    mock_coordinator.write_target_serials = []
    mgr = _make_manager(mock_hass, mock_coordinator)
    mgr._enabled = True
    assert mgr.mode == "idle"


def test_quality_no_age_check_when_max_age_none(mock_hass, mock_coordinator):
    mock_hass.states.get.return_value = _price_state("0.15", age_seconds=99999)
    mgr = _make_manager(mock_hass, mock_coordinator, price_max_age=None)
    quality, _ = mgr._compute_price_quality()
    assert quality == QUALITY_OK


# ── Schedule ────────────────────────────────────────────────────────────────


def test_is_in_schedule_no_restriction(mock_hass, mock_coordinator):
    mgr = _make_manager(mock_hass, mock_coordinator, start_hour=None, end_hour=None)
    assert mgr._is_in_schedule() is True


@pytest.mark.parametrize("hour", [8, 12, 21])
def test_is_in_schedule_normal_range(hour, mock_hass, mock_coordinator):
    mgr = _make_manager(mock_hass, mock_coordinator, start_hour=7, end_hour=22)
    with patch("custom_components.sunsynk.tariff.dt_util") as mock_dt:
        mock_dt.now.return_value = MagicMock(hour=hour)
        assert mgr._is_in_schedule() is True


@pytest.mark.parametrize("hour", [5, 6])
def test_is_outside_schedule_normal_range(hour, mock_hass, mock_coordinator):
    mgr = _make_manager(mock_hass, mock_coordinator, start_hour=7, end_hour=22)
    with patch("custom_components.sunsynk.tariff.dt_util") as mock_dt:
        mock_dt.now.return_value = MagicMock(hour=hour)
        assert mgr._is_in_schedule() is False


@pytest.mark.parametrize("hour", [22, 23, 0, 3, 5])
def test_is_in_schedule_midnight_wrap(hour, mock_hass, mock_coordinator):
    mgr = _make_manager(mock_hass, mock_coordinator, start_hour=22, end_hour=6)
    with patch("custom_components.sunsynk.tariff.dt_util") as mock_dt:
        mock_dt.now.return_value = MagicMock(hour=hour)
        assert mgr._is_in_schedule() is True


@pytest.mark.parametrize("hour", [6, 10, 18, 21])
def test_is_outside_schedule_midnight_wrap(hour, mock_hass, mock_coordinator):
    mgr = _make_manager(mock_hass, mock_coordinator, start_hour=22, end_hour=6)
    with patch("custom_components.sunsynk.tariff.dt_util") as mock_dt:
        mock_dt.now.return_value = MagicMock(hour=hour)
        assert mgr._is_in_schedule() is False


# ── Charging evaluation ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_evaluate_starts_charging(mock_hass, mock_coordinator):
    mock_hass.states.get.return_value = _price_state("0.05")
    mock_coordinator.data = {"TEST123": {"battery": {"soc": 50}}}
    mgr = _make_manager(mock_hass, mock_coordinator, cheap_threshold=0.10, target_soc=90)
    mgr._enabled = True
    mgr._price_quality = QUALITY_OK

    await mgr._evaluate_charging("TEST123", price=0.05, soc=50.0, in_schedule=True)

    mock_coordinator.async_write_setting.assert_awaited_once_with(
        "TEST123", "chargeCurrent", 100
    )
    assert mgr.is_charging_active_for("TEST123")


@pytest.mark.asyncio
async def test_evaluate_does_not_charge_above_threshold(mock_hass, mock_coordinator):
    mgr = _make_manager(mock_hass, mock_coordinator, cheap_threshold=0.10)
    mgr._enabled = True

    await mgr._evaluate_charging("TEST123", price=0.20, soc=50.0, in_schedule=True)

    mock_coordinator.async_write_setting.assert_not_awaited()
    assert not mgr.is_charging_active_for("TEST123")


@pytest.mark.asyncio
async def test_evaluate_does_not_charge_when_soc_at_target(mock_hass, mock_coordinator):
    mgr = _make_manager(mock_hass, mock_coordinator, cheap_threshold=0.10, target_soc=90)
    mgr._enabled = True

    await mgr._evaluate_charging("TEST123", price=0.05, soc=90.0, in_schedule=True)

    mock_coordinator.async_write_setting.assert_not_awaited()
    assert not mgr.is_charging_active_for("TEST123")


@pytest.mark.asyncio
async def test_evaluate_stops_charging_when_price_rises(mock_hass, mock_coordinator):
    mgr = _make_manager(mock_hass, mock_coordinator, cheap_threshold=0.10, normal_charge=50)
    mgr._enabled = True
    mgr._charging_active_serials.add("TEST123")

    await mgr._evaluate_charging("TEST123", price=0.20, soc=50.0, in_schedule=True)

    mock_coordinator.async_write_setting.assert_awaited_once_with(
        "TEST123", "chargeCurrent", 50
    )
    assert not mgr.is_charging_active_for("TEST123")


@pytest.mark.asyncio
async def test_evaluate_stops_charging_outside_schedule(mock_hass, mock_coordinator):
    mgr = _make_manager(mock_hass, mock_coordinator, cheap_threshold=0.10, normal_charge=50)
    mgr._enabled = True
    mgr._charging_active_serials.add("TEST123")

    await mgr._evaluate_charging("TEST123", price=0.05, soc=50.0, in_schedule=False)

    mock_coordinator.async_write_setting.assert_awaited_once_with(
        "TEST123", "chargeCurrent", 50
    )
    assert not mgr.is_charging_active_for("TEST123")


# ── Discharging evaluation ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_evaluate_starts_discharging(mock_hass, mock_coordinator):
    mock_hass.states.get.return_value = _price_state("0.40")
    mgr = _make_manager(
        mock_hass, mock_coordinator, expensive_threshold=0.30, discharge_min_soc=10
    )
    mgr._enabled = True
    mgr._price_quality = QUALITY_OK

    await mgr._evaluate_discharging("TEST123", price=0.40, soc=80.0, in_schedule=True)

    mock_coordinator.async_write_setting.assert_awaited_once_with(
        "TEST123", "dischargeCurrent", 100
    )
    assert mgr.is_discharging_active_for("TEST123")


@pytest.mark.asyncio
async def test_evaluate_does_not_discharge_below_threshold(mock_hass, mock_coordinator):
    mgr = _make_manager(mock_hass, mock_coordinator, expensive_threshold=0.30)
    mgr._enabled = True

    await mgr._evaluate_discharging("TEST123", price=0.20, soc=80.0, in_schedule=True)

    mock_coordinator.async_write_setting.assert_not_awaited()
    assert not mgr.is_discharging_active_for("TEST123")


@pytest.mark.asyncio
async def test_evaluate_does_not_discharge_at_min_soc(mock_hass, mock_coordinator):
    mgr = _make_manager(
        mock_hass, mock_coordinator, expensive_threshold=0.30, discharge_min_soc=10
    )
    mgr._enabled = True

    await mgr._evaluate_discharging("TEST123", price=0.40, soc=10.0, in_schedule=True)

    mock_coordinator.async_write_setting.assert_not_awaited()
    assert not mgr.is_discharging_active_for("TEST123")


@pytest.mark.asyncio
async def test_evaluate_stops_discharging_when_price_drops(mock_hass, mock_coordinator):
    mgr = _make_manager(
        mock_hass, mock_coordinator, expensive_threshold=0.30, normal_discharge=50
    )
    mgr._enabled = True
    mgr._discharging_active_serials.add("TEST123")

    await mgr._evaluate_discharging("TEST123", price=0.20, soc=50.0, in_schedule=True)

    mock_coordinator.async_write_setting.assert_awaited_once_with(
        "TEST123", "dischargeCurrent", 50
    )
    assert not mgr.is_discharging_active_for("TEST123")


# ── set_enabled ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_set_enabled_false_restores_currents(mock_hass, mock_coordinator):
    tasks = []
    mock_hass.async_create_task = lambda coro: tasks.append(asyncio.create_task(coro))
    mock_hass.services.async_call = AsyncMock()
    mgr = _make_manager(mock_hass, mock_coordinator, normal_charge=50, normal_discharge=50)
    mgr._enabled = True
    mgr._charging_active_serials.add("TEST123")
    mgr._discharging_active_serials.add("TEST123")

    mgr.set_enabled(False)
    await asyncio.gather(*tasks)

    assert not mgr.is_enabled
    assert not mgr.is_charging_active
    assert not mgr.is_discharging_active
    mock_coordinator.async_write_settings.assert_awaited_once_with(
        "TEST123",
        {"chargeCurrent": 50, "dischargeCurrent": 50},
    )


def test_set_enabled_notifies_listeners(mock_hass, mock_coordinator):
    mock_hass.async_create_task = MagicMock()
    mgr = _make_manager(mock_hass, mock_coordinator)
    fired = []
    mgr.async_add_listener(lambda: fired.append(True))

    mgr.set_enabled(False)

    assert len(fired) == 1


def test_listener_unsub(mock_hass, mock_coordinator):
    mock_hass.async_create_task = MagicMock()
    mgr = _make_manager(mock_hass, mock_coordinator)
    fired = []
    unsub = mgr.async_add_listener(lambda: fired.append(True))
    unsub()

    mgr.set_enabled(False)

    assert len(fired) == 0


# ── No-op when thresholds not configured ────────────────────────────────────


@pytest.mark.asyncio
async def test_no_charging_when_threshold_none(mock_hass, mock_coordinator):
    mgr = _make_manager(mock_hass, mock_coordinator, cheap_threshold=None)
    mgr._enabled = True

    await mgr._evaluate_charging("TEST123", price=0.05, soc=50.0, in_schedule=True)

    mock_coordinator.async_write_setting.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_discharging_when_threshold_none(mock_hass, mock_coordinator):
    mgr = _make_manager(mock_hass, mock_coordinator, expensive_threshold=None)
    mgr._enabled = True

    await mgr._evaluate_discharging("TEST123", price=0.50, soc=80.0, in_schedule=True)


# ── Separate import/export price entities (#16) ──────────────────────────────


def _states_for(mapping: dict[str, MagicMock]):
    """A hass.states.get side_effect that resolves per entity_id."""
    return lambda entity_id: mapping.get(entity_id)


def test_export_price_entity_defaults_to_price_entity_when_unset(mock_hass, mock_coordinator):
    mgr = _make_manager(mock_hass, mock_coordinator)
    assert mgr.export_price_entity == mgr.price_entity == "sensor.electricity_price"


def test_export_price_entity_used_when_given(mock_hass, mock_coordinator):
    mgr = _make_manager(mock_hass, mock_coordinator, export_price_entity="sensor.export_price")
    assert mgr.price_entity == "sensor.electricity_price"
    assert mgr.export_price_entity == "sensor.export_price"


def test_compute_price_quality_reads_the_requested_entity(mock_hass, mock_coordinator):
    mock_hass.states.get.side_effect = _states_for({
        "sensor.electricity_price": _price_state("0.10"),
        "sensor.export_price": None,
    })
    mgr = _make_manager(mock_hass, mock_coordinator, export_price_entity="sensor.export_price")

    import_quality, _ = mgr._compute_price_quality(mgr.price_entity)
    export_quality, _ = mgr._compute_price_quality(mgr.export_price_entity)

    assert import_quality == QUALITY_OK
    assert export_quality == QUALITY_NOT_FOUND


@pytest.mark.asyncio
async def test_evaluate_charge_takes_priority_when_both_prices_trigger(
    mock_hass, mock_coordinator
):
    """A target never receives simultaneous charge and discharge commands."""
    mock_hass.states.get.side_effect = _states_for({
        "sensor.import_price": _price_state("0.05"),   # cheap → should charge
        "sensor.export_price": _price_state("0.50"),   # expensive → should discharge
    })
    mgr = TariffChargingManager(
        hass=mock_hass,
        coordinator=mock_coordinator,
        price_entity="sensor.import_price",
        cheap_threshold=0.10,
        cheap_current=100,
        normal_charge_current=50,
        target_soc=90,
        expensive_threshold=0.30,
        peak_discharge_current=100,
        normal_discharge_current=50,
        discharge_min_soc=10,
        export_price_entity="sensor.export_price",
    )
    mgr._enabled = True

    await mgr._evaluate()

    assert mgr.is_charging_active_for("TEST123")
    assert not mgr.is_discharging_active_for("TEST123")
    mock_coordinator.async_write_setting.assert_any_await("TEST123", "chargeCurrent", 100)


@pytest.mark.asyncio
async def test_evaluate_never_writes_to_a_parallel_slave(mock_hass, mock_coordinator):
    """Regression coverage (#21): _evaluate() used to loop over every
    *configured* serial, so a parallel group's slave got its own
    (redundant) chargeCurrent/dischargeCurrent write alongside the
    master's — a second write landing right behind the first was, on its
    own, enough to make the master intermittently reject/revert one of
    them. Must iterate write_target_serials (slave already collapsed into
    its master), not coordinator.serials.
    """
    mock_hass.states.get.return_value = _price_state("0.05")
    mock_coordinator.serials = ["SLAVE1", "MASTER1"]
    mock_coordinator.write_target_serials = ["MASTER1"]
    mock_coordinator.data = {
        "MASTER1": {"battery": {"soc": 50}},
        "SLAVE1": {"battery": {"soc": 50}},
    }
    mgr = _make_manager(mock_hass, mock_coordinator, cheap_threshold=0.10, target_soc=90)
    mgr._enabled = True

    await mgr._evaluate()

    called_serials = {c.args[0] for c in mock_coordinator.async_write_setting.call_args_list}
    assert called_serials == {"MASTER1"}


@pytest.mark.asyncio
async def test_evaluate_tracks_independent_inverters_separately(
    mock_hass, mock_coordinator
):
    """Each physical target starts and stops from its own SOC state."""
    mock_hass.states.get.return_value = _price_state("0.05")
    mock_coordinator.write_target_serials = ["INV1", "INV2"]
    mock_coordinator.data = {
        "INV1": {"battery": {"soc": 50}},
        "INV2": {"battery": {"soc": 50}},
    }
    mgr = _make_manager(mock_hass, mock_coordinator, target_soc=90)
    mgr._enabled = True

    await mgr._evaluate()

    mock_coordinator.async_write_setting.assert_any_await(
        "INV1", "chargeCurrent", 100
    )
    mock_coordinator.async_write_setting.assert_any_await(
        "INV2", "chargeCurrent", 100
    )
    assert mgr.per_inverter_modes == {"INV1": "charging", "INV2": "charging"}

    mock_coordinator.async_write_setting.reset_mock()
    mock_coordinator.data["INV1"]["battery"]["soc"] = 95
    await mgr._evaluate()

    mock_coordinator.async_write_setting.assert_awaited_once_with(
        "INV1", "chargeCurrent", 50
    )
    assert mgr.per_inverter_modes == {"INV1": "idle", "INV2": "charging"}
    assert mgr.mode == "mixed"


@pytest.mark.asyncio
@pytest.mark.parametrize("soc", [None, "bad", float("nan"), float("inf"), -1, 101])
async def test_evaluate_fails_closed_for_untrustworthy_soc(
    mock_hass, mock_coordinator, soc
):
    mock_hass.states.get.return_value = _price_state("0.05")
    mock_coordinator.data = {"TEST123": {"battery": {"soc": soc}}}
    mgr = _make_manager(mock_hass, mock_coordinator)
    mgr._enabled = True

    await mgr._evaluate()

    mock_coordinator.async_write_setting.assert_not_awaited()
    assert mgr.mode == "idle"
    assert mgr.soc_quality_by_inverter["TEST123"] != QUALITY_OK


@pytest.mark.asyncio
async def test_missing_soc_stops_active_mode_and_restores_normal_current(
    mock_hass, mock_coordinator
):
    mock_hass.states.get.return_value = _price_state("0.05")
    mock_coordinator.data = {"TEST123": {"battery": {}}}
    mgr = _make_manager(mock_hass, mock_coordinator)
    mgr._enabled = True
    mgr._charging_active_serials.add("TEST123")

    await mgr._evaluate()

    mock_coordinator.async_write_setting.assert_awaited_once_with(
        "TEST123", "chargeCurrent", 50
    )
    assert mgr.mode == "idle"
    assert mgr.soc_quality_by_inverter == {"TEST123": "missing"}


@pytest.mark.asyncio
async def test_bad_soc_on_one_inverter_does_not_block_a_healthy_inverter(
    mock_hass, mock_coordinator
):
    mock_hass.states.get.return_value = _price_state("0.05")
    mock_coordinator.write_target_serials = ["INV1", "INV2"]
    mock_coordinator.data = {
        "INV1": {"battery": {}},
        "INV2": {"battery": {"soc": 50}},
    }
    mgr = _make_manager(mock_hass, mock_coordinator)
    mgr._enabled = True

    await mgr._evaluate()

    mock_coordinator.async_write_setting.assert_awaited_once_with(
        "INV2", "chargeCurrent", 100
    )
    assert mgr.per_inverter_modes == {"INV1": "idle", "INV2": "charging"}


@pytest.mark.asyncio
async def test_evaluate_stops_only_charging_when_import_quality_bad(mock_hass, mock_coordinator):
    """Bad import price data must pause charging without touching an
    already-active discharge driven by a perfectly healthy export price.
    """
    mock_hass.states.get.side_effect = _states_for({
        "sensor.import_price": None,  # entity missing → NOT_FOUND
        "sensor.export_price": _price_state("0.50"),
    })
    mgr = TariffChargingManager(
        hass=mock_hass,
        coordinator=mock_coordinator,
        price_entity="sensor.import_price",
        cheap_threshold=0.10,
        cheap_current=100,
        normal_charge_current=50,
        target_soc=90,
        expensive_threshold=0.30,
        peak_discharge_current=100,
        normal_discharge_current=50,
        discharge_min_soc=10,
        export_price_entity="sensor.export_price",
    )
    mgr._enabled = True
    mgr._charging_active_serials.add("TEST123")

    await mgr._evaluate()

    assert not mgr.is_charging_active_for("TEST123")
    assert mgr.is_discharging_active_for("TEST123")
    mock_coordinator.async_write_setting.assert_any_await("TEST123", "chargeCurrent", 50)
    mock_coordinator.async_write_setting.assert_any_await("TEST123", "dischargeCurrent", 100)


@pytest.mark.asyncio
async def test_stop_charging_is_a_noop_when_already_inactive(mock_hass, mock_coordinator):
    mgr = _make_manager(mock_hass, mock_coordinator)

    await mgr._stop_charging("TEST123", "test reason")

    mock_coordinator.async_write_setting.assert_not_awaited()


@pytest.mark.asyncio
async def test_stop_discharging_is_a_noop_when_already_inactive(mock_hass, mock_coordinator):
    mgr = _make_manager(mock_hass, mock_coordinator)

    await mgr._stop_discharging("TEST123", "test reason")

    mock_coordinator.async_write_setting.assert_not_awaited()

    mock_coordinator.async_write_setting.assert_not_awaited()


@pytest.mark.asyncio
async def test_shutdown_attempts_every_inverter_after_restore_failure(
    mock_hass, mock_coordinator
):
    mock_coordinator.write_target_serials = ["INV1", "INV2"]

    async def _write(serial, settings):
        if serial == "INV1":
            raise RuntimeError("INV1 offline")

    mock_coordinator.async_write_settings.side_effect = _write
    mgr = _make_manager(mock_hass, mock_coordinator, normal_charge=50)
    mgr._enabled = True
    mgr._charging_active_serials.update({"INV1", "INV2"})

    success = await mgr.async_shutdown()

    assert success is False
    assert mock_coordinator.async_write_settings.await_count == 2
    mock_coordinator.async_write_settings.assert_any_await(
        "INV2", {"chargeCurrent": 50}
    )
    # INV2 was restored and released; INV1 still owes a restore.
    assert mgr.is_charging_active_for("INV1")
    assert not mgr.is_charging_active_for("INV2")


# ── Export price staleness (#16, #21, #30) ──────────────────────────────────
#
# Export rates are often fixed for months (e.g. Octopus Outgoing), so their
# sensor rarely updates. Age-checking it marked the export price "stale" and
# silently blocked every discharge. Only the import price is age-checked.

_TWO_DAYS = 2 * 24 * 3600


def test_separate_export_price_is_not_age_checked(mock_hass, mock_coordinator):
    mock_hass.states.get.side_effect = _states_for({
        "sensor.electricity_price": _price_state("0.10"),
        "sensor.export_price": _price_state("0.15", age_seconds=_TWO_DAYS),
    })
    mgr = _make_manager(
        mock_hass, mock_coordinator, price_max_age=30, export_price_entity="sensor.export_price"
    )

    export_quality, _ = mgr._compute_price_quality(mgr.export_price_entity)

    assert export_quality == QUALITY_OK


def test_import_price_is_still_age_checked(mock_hass, mock_coordinator):
    mock_hass.states.get.side_effect = _states_for({
        "sensor.electricity_price": _price_state("0.10", age_seconds=_TWO_DAYS),
        "sensor.export_price": _price_state("0.15"),
    })
    mgr = _make_manager(
        mock_hass, mock_coordinator, price_max_age=30, export_price_entity="sensor.export_price"
    )

    import_quality, _ = mgr._compute_price_quality(mgr.price_entity)

    assert import_quality == QUALITY_STALE


def test_single_price_sensor_is_still_age_checked(mock_hass, mock_coordinator):
    """With one sensor for both sides, it is the import price: unchanged."""
    mock_hass.states.get.return_value = _price_state("0.40", age_seconds=_TWO_DAYS)
    mgr = _make_manager(mock_hass, mock_coordinator, price_max_age=30)

    export_quality, _ = mgr._compute_price_quality(mgr.export_price_entity)

    assert export_quality == QUALITY_STALE


def test_unavailable_export_price_still_blocks(mock_hass, mock_coordinator):
    mock_hass.states.get.side_effect = _states_for({
        "sensor.electricity_price": _price_state("0.10"),
        "sensor.export_price": _price_state("unavailable"),
    })
    mgr = _make_manager(mock_hass, mock_coordinator, export_price_entity="sensor.export_price")

    export_quality, _ = mgr._compute_price_quality(mgr.export_price_entity)

    assert export_quality == QUALITY_UNAVAILABLE


@pytest.mark.asyncio
async def test_fixed_export_rate_still_starts_discharging(mock_hass, mock_coordinator):
    """The reporters' case: a fixed export rate that last changed days ago."""
    mock_hass.states.get.side_effect = _states_for({
        "sensor.electricity_price": _price_state("0.20"),
        "sensor.export_price": _price_state("0.40", age_seconds=_TWO_DAYS),
    })
    mgr = _make_manager(
        mock_hass,
        mock_coordinator,
        price_max_age=30,
        expensive_threshold=0.30,
        export_price_entity="sensor.export_price",
    )
    mgr._enabled = True

    await mgr._evaluate()

    assert mgr.export_price_quality == QUALITY_OK
    assert mgr.is_discharging_active_for("TEST123")


# ── Staleness follows the last report, not the last value change (#21) ─────


def test_unchanged_but_regularly_reported_price_is_not_stale(mock_hass, mock_coordinator):
    """An off-peak rate unchanged for hours, still reported every minute."""
    mock_hass.states.get.return_value = _price_state(
        "0.07", age_seconds=5 * 3600, reported_seconds=60
    )
    mgr = _make_manager(mock_hass, mock_coordinator, price_max_age=90)

    quality, _ = mgr._compute_price_quality()

    assert quality == QUALITY_OK


def test_price_no_longer_reported_is_stale(mock_hass, mock_coordinator):
    mock_hass.states.get.return_value = _price_state(
        "0.07", age_seconds=5 * 3600, reported_seconds=5 * 3600
    )
    mgr = _make_manager(mock_hass, mock_coordinator, price_max_age=90)

    quality, detail = mgr._compute_price_quality()

    assert quality == QUALITY_STALE
    assert detail.startswith("Last reported 300 min ago")


def test_older_home_assistant_falls_back_to_last_updated(mock_hass, mock_coordinator):
    """HA before 2024.3 has no last_reported."""
    state = _price_state("0.07", age_seconds=5 * 3600)
    del state.last_reported
    mock_hass.states.get.return_value = state
    mgr = _make_manager(mock_hass, mock_coordinator, price_max_age=90)

    quality, _ = mgr._compute_price_quality()

    assert quality == QUALITY_STALE



def test_hand_set_helper_price_is_never_stale(mock_hass, mock_coordinator):
    """#21: a tester's input_number price, unchanged since a restart 2 h ago."""
    mock_hass.states.get.return_value = _price_state(
        "0.40", age_seconds=2 * 3600, reported_seconds=2 * 3600
    )
    mgr = TariffChargingManager(
        hass=mock_hass,
        coordinator=mock_coordinator,
        price_entity="input_number.test_price",
        cheap_threshold=0.10,
        cheap_current=100,
        normal_charge_current=50,
        target_soc=90,
        expensive_threshold=0.30,
        peak_discharge_current=100,
        normal_discharge_current=50,
        discharge_min_soc=10,
        price_max_age_minutes=90,
    )

    quality, _ = mgr._compute_price_quality()

    assert quality == QUALITY_OK



@pytest.mark.parametrize("hour", [0, 7, 13, 23])
def test_equal_start_and_end_hour_means_the_whole_day(mock_hass, mock_coordinator, hour):
    """#21: "00:00-00:00" kept Tariff Manager idle all day."""
    mgr = _make_manager(mock_hass, mock_coordinator, start_hour=0, end_hour=0)

    with patch(
        "custom_components.sunsynk.tariff.dt_util.now",
        return_value=datetime(2026, 10, 12, hour, 30, tzinfo=timezone.utc),
    ):
        assert mgr._is_in_schedule() is True


@pytest.mark.asyncio
async def test_export_above_threshold_discharges_with_whole_day_schedule(
    mock_hass, mock_coordinator
):
    """The reporter's case: hours 0 to 0, export helper 0.18 > 0.16."""
    mock_hass.states.get.side_effect = _states_for({
        "sensor.electricity_price": _price_state("0.26"),
        "input_number.electric_export_rate": _price_state("0.18"),
    })
    mgr = _make_manager(
        mock_hass,
        mock_coordinator,
        expensive_threshold=0.16,
        start_hour=0,
        end_hour=0,
        export_price_entity="input_number.electric_export_rate",
    )
    mgr._enabled = True
    listener = MagicMock()
    mgr.async_add_listener(listener)

    await mgr._evaluate()

    assert mgr.is_discharging_active_for("TEST123")
    listener.assert_called()


# ── Why Tariff Manager is or isn't acting (#21) ─────────────────────────────


async def _decide(mock_hass, mock_coordinator, *, import_price="0.30", export_price="0.18",
                  soc=80, **kwargs):
    mock_hass.states.get.side_effect = _states_for({
        "sensor.electricity_price": _price_state(import_price),
        "input_number.export": _price_state(export_price),
    })
    mock_coordinator.data["TEST123"]["battery"]["soc"] = soc
    options = {"expensive_threshold": 0.16, "export_price_entity": "input_number.export"}
    options.update(kwargs)
    mgr = _make_manager(mock_hass, mock_coordinator, **options)
    mgr._enabled = True
    await mgr._evaluate()
    return mgr.decisions["TEST123"]


@pytest.mark.asyncio
async def test_decision_shows_prices_soc_and_discharging(mock_hass, mock_coordinator):
    decision = await _decide(mock_hass, mock_coordinator)

    assert decision["import_price"] == 0.30
    assert decision["export_price"] == 0.18
    assert decision["battery_soc"] == 80
    assert decision["discharging"] == "discharging"
    assert decision["charging"] == "import price 0.3 is above the cheap threshold 0.1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"soc": 15, "discharge_min_soc": 20}, "battery 15% is not above the minimum 20%"),
        ({"export_price": "0.10"}, "export price 0.1 is below the expensive threshold 0.16"),
        ({"peak_discharge": None}, "not set up (needs an expensive threshold and a peak discharge current)"),
        ({"start_hour": 1, "end_hour": 2}, "outside the active hours"),
    ],
)
async def test_decision_explains_why_not_discharging(
    mock_hass, mock_coordinator, kwargs, expected
):
    with patch(
        "custom_components.sunsynk.tariff.dt_util.now",
        return_value=datetime(2026, 10, 13, 22, 7, tzinfo=timezone.utc),
    ):
        decision = await _decide(mock_hass, mock_coordinator, **kwargs)

    assert decision["discharging"] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"import_price": "0.05"}, "charging"),
        ({"import_price": "0.05", "soc": 95}, "battery 95% has reached the target 90%"),
        ({"cheap_threshold": None}, "not set up (needs a cheap threshold and a cheap-rate charge current)"),
        ({"import_price": "0.05", "start_hour": 1, "end_hour": 2}, "outside the active hours"),
    ],
)
async def test_decision_explains_charging(mock_hass, mock_coordinator, kwargs, expected):
    with patch(
        "custom_components.sunsynk.tariff.dt_util.now",
        return_value=datetime(2026, 10, 13, 22, 7, tzinfo=timezone.utc),
    ):
        decision = await _decide(mock_hass, mock_coordinator, **kwargs)

    assert decision["charging"] == expected


@pytest.mark.asyncio
async def test_charging_takes_priority_is_explained(mock_hass, mock_coordinator):
    decision = await _decide(
        mock_hass, mock_coordinator, import_price="0.05", export_price="0.40",
        expensive_threshold=0.30,
    )

    assert decision["charging"] == "charging"
    assert decision["discharging"] == "charging takes priority"


@pytest.mark.asyncio
async def test_decision_explains_missing_soc_and_bad_prices(mock_hass, mock_coordinator):
    decision = await _decide(mock_hass, mock_coordinator, soc=None)
    assert decision["charging"] == decision["discharging"] == "battery SOC unavailable (missing)"

    decision = await _decide(
        mock_hass, mock_coordinator, import_price="unavailable", export_price="bad"
    )
    assert decision["charging"].startswith("import price unavailable")
    assert decision["discharging"].startswith("export price invalid")


@pytest.mark.asyncio
async def test_decision_without_any_price_reading(mock_hass, mock_coordinator):
    mgr = _make_manager(mock_hass, mock_coordinator)
    with patch.object(mgr, "_read_price", return_value=None), patch.object(
        mgr, "_compute_price_quality", return_value=(QUALITY_OK, "ok")
    ):
        mgr._enabled = True
        await mgr._evaluate()

    assert mgr.decisions["TEST123"]["charging"] == "no import price"
    assert mgr.decisions["TEST123"]["discharging"] == "no export price"


def test_a_failing_listener_does_not_stop_the_others(mock_hass, mock_coordinator, caplog):
    """#21: the switch must still update if another entity's update fails."""
    _consume_created_tasks(mock_hass)
    mgr = _make_manager(mock_hass, mock_coordinator)
    broken = MagicMock(side_effect=RuntimeError("boom"))
    switch = MagicMock()
    mgr.async_add_listener(broken)
    mgr.async_add_listener(switch)

    mgr.set_enabled(True)

    switch.assert_called()
    assert "a listener failed to update" in caplog.text



@pytest.mark.asyncio
async def test_evaluation_error_is_logged_not_left_unretrieved(
    mock_hass, mock_coordinator, caplog
):
    mgr = _make_manager(mock_hass, mock_coordinator)
    mgr._enabled = True
    with patch.object(mgr, "_evaluate_locked", AsyncMock(side_effect=RuntimeError("x"))):
        await mgr._evaluate()

    assert "Tariff Manager: evaluation failed" in caplog.text



@pytest.mark.parametrize("value", ["inf", "-inf", "nan"])
def test_non_finite_price_is_invalid(mock_hass, mock_coordinator, value):
    """An "inf" price would read as above every threshold and start a
    discharge. Spotted by David (@davidatjourney)."""
    mock_hass.states.get.return_value = _price_state(value)
    mgr = _make_manager(mock_hass, mock_coordinator)

    quality, _ = mgr._compute_price_quality()

    assert quality == QUALITY_INVALID
    assert mgr._read_price("sensor.electricity_price") is None



# ── Safety review: stale SOC, failed restore, mid-mode current change ──────


@pytest.mark.asyncio
async def test_no_decision_on_soc_kept_from_a_failed_refresh(mock_hass, mock_coordinator):
    """A failed refresh keeps the previous data: a 50% SOC that may be hours
    old must not start a 100 A charge."""
    mock_hass.states.get.return_value = _price_state("0.05")
    mock_coordinator.data["TEST123"]["battery"]["soc"] = 50
    mock_coordinator.last_update_success = False
    mgr = _make_manager(mock_hass, mock_coordinator)
    mgr._enabled = True

    await mgr._evaluate()

    mock_coordinator.async_write_setting.assert_not_awaited()
    assert not mgr.is_charging_active_for("TEST123")
    assert mgr.decisions["TEST123"]["charging"] == "battery SOC unavailable (stale)"


@pytest.mark.asyncio
async def test_active_charge_stops_when_refreshes_start_failing(mock_hass, mock_coordinator):
    mock_hass.states.get.return_value = _price_state("0.05")
    mgr = _make_manager(mock_hass, mock_coordinator, normal_charge=50)
    mgr._enabled = True
    await mgr._evaluate()
    assert mgr.is_charging_active_for("TEST123")

    mock_coordinator.last_update_success = False
    await mgr._evaluate()

    assert not mgr.is_charging_active_for("TEST123")
    mock_coordinator.async_write_setting.assert_awaited_with("TEST123", "chargeCurrent", 50)


@pytest.mark.asyncio
async def test_failed_restore_is_retried_on_the_next_update(mock_hass, mock_coordinator):
    """Disabling while charging: a failed restore must keep ownership and be
    retried, not report success without sending anything."""
    _consume_created_tasks(mock_hass)
    mgr = _make_manager(mock_hass, mock_coordinator, normal_charge=50)
    mgr._charging_active_serials.add("TEST123")
    mock_coordinator.async_write_settings.side_effect = [RuntimeError("API down"), None]

    assert await mgr._async_restore_active_currents() is False
    assert mgr.is_charging_active_for("TEST123")

    mgr._enabled = False
    await mgr._evaluate()  # next coordinator update, Tariff Manager disabled

    assert not mgr.is_charging_active_for("TEST123")
    assert mock_coordinator.async_write_settings.await_count == 2
    mock_coordinator.async_write_settings.assert_awaited_with(
        "TEST123", {"chargeCurrent": 50}
    )


@pytest.mark.asyncio
async def test_disabled_without_owned_currents_does_nothing(mock_hass, mock_coordinator):
    mgr = _make_manager(mock_hass, mock_coordinator)
    await mgr._evaluate()
    mock_coordinator.async_write_settings.assert_not_awaited()


@pytest.mark.asyncio
async def test_changed_charge_current_is_applied_while_charging(mock_hass, mock_coordinator):
    mock_hass.states.get.return_value = _price_state("0.05")
    mgr = _make_manager(mock_hass, mock_coordinator, cheap_current=100)
    mgr._enabled = True
    await mgr._evaluate()
    mock_coordinator.async_write_setting.assert_awaited_with("TEST123", "chargeCurrent", 100)

    mgr._cheap_current = 80
    await mgr._evaluate()

    mock_coordinator.async_write_setting.assert_awaited_with("TEST123", "chargeCurrent", 80)
    mock_coordinator.async_write_setting.reset_mock()
    await mgr._evaluate()  # unchanged: nothing sent
    mock_coordinator.async_write_setting.assert_not_awaited()


@pytest.mark.asyncio
async def test_changed_discharge_current_is_applied_while_discharging(
    mock_hass, mock_coordinator
):
    mock_hass.states.get.return_value = _price_state("0.40")
    mgr = _make_manager(mock_hass, mock_coordinator, peak_discharge=100)
    mgr._enabled = True
    await mgr._evaluate()
    assert mgr.is_discharging_active_for("TEST123")

    mgr._peak_discharge_current = 60
    await mgr._evaluate()

    mock_coordinator.async_write_setting.assert_awaited_with(
        "TEST123", "dischargeCurrent", 60
    )


@pytest.mark.asyncio
async def test_failed_write_on_one_inverter_does_not_skip_the_next(
    mock_hass, mock_coordinator
):
    """2.0 beta audit R6: A's API error must not leave B forced on."""
    mock_hass.states.get.return_value = _price_state("0.05")
    mock_coordinator.write_target_serials = ["A", "B"]
    mock_coordinator.data = {"A": {"battery": {}}, "B": {"battery": {}}}

    async def write(serial, key, value):
        if serial == "A":
            raise SunsynkApiError("only A is failing")

    mock_coordinator.async_write_setting.side_effect = write
    mgr = _make_manager(mock_hass, mock_coordinator)
    mgr._enabled = True
    mgr._charging_active_serials = {"A", "B"}

    await mgr._evaluate()

    assert "B" in {c.args[0] for c in mock_coordinator.async_write_setting.call_args_list}
    assert mgr._charging_active_serials == {"A"}

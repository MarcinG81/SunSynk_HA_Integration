"""Charge / discharge now, started by hand from an automation (#39)."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util

from custom_components import sunsynk as integration
from custom_components.sunsynk.const import DOMAIN
from custom_components.sunsynk.tariff import (
    MANUAL_CHARGE,
    MANUAL_DISCHARGE,
    ManualSession,
)
from tests.test_tariff import _make_manager, _price_state

SERIAL = "TEST123"


@pytest.fixture(autouse=True)
def _timer():
    with patch(
        "custom_components.sunsynk.tariff.async_track_point_in_utc_time"
    ) as track:
        track.return_value = MagicMock()
        yield track


def _written(coordinator):
    return [c.args[1:] for c in coordinator.async_write_setting.call_args_list]


@pytest.mark.asyncio
async def test_charge_now_works_with_tariff_manager_off(mock_hass, mock_coordinator):
    manager = _make_manager(mock_hass, mock_coordinator, cheap_current=100)
    assert manager._enabled is False

    await manager.async_start_manual(SERIAL, MANUAL_CHARGE)

    assert _written(mock_coordinator) == [("chargeCurrent", 100)]
    # The Virtual Slot Scheduler sees it as a charge override.
    assert manager.is_charging_active_for(SERIAL)
    assert manager.mode_for(SERIAL) == "manual_charge"
    assert manager.mode == "manual_charge"
    assert manager.manual_sessions == {SERIAL: {"mode": "charge", "until": None}}

    # An update while Tariff Manager is off doesn't undo it.
    await manager._evaluate()
    assert manager.is_charging_active_for(SERIAL)
    assert _written(mock_coordinator) == [("chargeCurrent", 100)]


@pytest.mark.asyncio
async def test_a_duration_ends_it_and_puts_the_normal_current_back(
    mock_hass, mock_coordinator, _timer
):
    manager = _make_manager(mock_hass, mock_coordinator, normal_discharge=50)
    start = dt_util.utcnow()

    await manager.async_start_manual(SERIAL, MANUAL_DISCHARGE, 30)

    until = _timer.call_args.args[2]
    assert timedelta(minutes=29) < until - start < timedelta(minutes=31)
    assert manager.manual_sessions[SERIAL]["until"] is not None
    assert manager.decisions[SERIAL]["manual"].startswith("discharge until ")

    # The timer fires; time is up.
    mock_hass.async_create_task.side_effect = lambda coroutine: coroutine.close()
    _timer.call_args.args[1](until)
    mock_hass.async_create_task.assert_called()
    with patch(
        "custom_components.sunsynk.tariff.dt_util.utcnow",
        return_value=until + timedelta(seconds=1),
    ):
        await manager._evaluate()

    assert not manager.is_discharging_active_for(SERIAL)
    assert _written(mock_coordinator)[-1] == ("dischargeCurrent", 50)
    assert manager.mode == "disabled"


@pytest.mark.asyncio
async def test_discharge_now_ends_at_the_minimum_soc(mock_hass, mock_coordinator):
    manager = _make_manager(mock_hass, mock_coordinator, discharge_min_soc=20)
    await manager.async_start_manual(SERIAL, MANUAL_DISCHARGE)
    assert manager.is_discharging_active_for(SERIAL)

    mock_coordinator.data[SERIAL]["battery"]["soc"] = 20
    await manager._evaluate()

    assert not manager.is_discharging_active_for(SERIAL)
    assert manager.manual_for(SERIAL) is None


@pytest.mark.asyncio
async def test_discharge_now_doesnt_start_below_the_minimum(mock_hass, mock_coordinator):
    mock_coordinator.data[SERIAL]["battery"]["soc"] = 10
    manager = _make_manager(mock_hass, mock_coordinator, discharge_min_soc=20)

    await manager.async_start_manual(SERIAL, MANUAL_DISCHARGE)

    mock_coordinator.async_write_setting.assert_not_called()
    assert manager.manual_for(SERIAL) is None


@pytest.mark.asyncio
async def test_by_hand_wins_over_the_price(mock_hass, mock_coordinator):
    """An export price above the threshold would discharge: a manual charge
    holds until it's stopped, then the price decides again."""
    mock_hass.states.get.return_value = _price_state("0.40")
    mock_hass.async_create_task.side_effect = lambda coroutine: coroutine.close()
    manager = _make_manager(mock_hass, mock_coordinator)
    manager._enabled = True
    await manager._evaluate()
    assert manager.is_discharging_active_for(SERIAL)

    await manager.async_start_manual(SERIAL, MANUAL_CHARGE)
    await manager._evaluate()

    assert manager.is_charging_active_for(SERIAL)
    assert not manager.is_discharging_active_for(SERIAL)

    await manager.async_stop_manual(SERIAL, MANUAL_CHARGE)
    assert not manager.is_charging_active_for(SERIAL)
    await manager._evaluate()
    assert manager.is_discharging_active_for(SERIAL)


@pytest.mark.asyncio
async def test_stopping_the_other_direction_does_nothing(mock_hass, mock_coordinator):
    manager = _make_manager(mock_hass, mock_coordinator)
    await manager.async_start_manual(SERIAL, MANUAL_CHARGE)

    await manager.async_stop_manual(SERIAL, MANUAL_DISCHARGE)
    await manager.async_stop_manual("OTHER", MANUAL_CHARGE)

    assert manager.is_charging_active_for(SERIAL)


@pytest.mark.asyncio
async def test_a_new_start_replaces_the_old_one(mock_hass, mock_coordinator, _timer):
    manager = _make_manager(mock_hass, mock_coordinator)
    await manager.async_start_manual(SERIAL, MANUAL_CHARGE, 60)
    first_timer = _timer.return_value

    await manager.async_start_manual(SERIAL, MANUAL_DISCHARGE)

    first_timer.assert_called_once_with()  # the old end cancelled
    assert not manager.is_charging_active_for(SERIAL)
    assert manager.is_discharging_active_for(SERIAL)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "setting"),
    [(MANUAL_CHARGE, "cheap_current"), (MANUAL_DISCHARGE, "peak_discharge")],
)
async def test_without_the_current_configured_it_is_refused(
    mock_hass, mock_coordinator, mode, setting
):
    manager = _make_manager(mock_hass, mock_coordinator, **{setting: None})

    with pytest.raises(ServiceValidationError, match="Tariff Manager options"):
        await manager.async_start_manual(SERIAL, mode)


@pytest.mark.asyncio
async def test_no_trustworthy_soc_ends_it(mock_hass, mock_coordinator):
    manager = _make_manager(mock_hass, mock_coordinator)
    await manager.async_start_manual(SERIAL, MANUAL_CHARGE)

    mock_coordinator.last_update_success = False
    await manager._evaluate()

    assert manager.manual_for(SERIAL) is None
    assert not manager.is_charging_active_for(SERIAL)


@pytest.mark.asyncio
async def test_switching_tariff_manager_off_stops_it(mock_hass, mock_coordinator):
    mock_hass.async_create_task.side_effect = lambda coroutine: coroutine.close()
    manager = _make_manager(mock_hass, mock_coordinator)
    await manager.async_start_manual(SERIAL, MANUAL_CHARGE, 30)

    manager.set_enabled(False)

    assert manager.manual_sessions == {}


@pytest.mark.asyncio
async def test_unload_stops_it_and_puts_the_currents_back(mock_hass, mock_coordinator):
    manager = _make_manager(mock_hass, mock_coordinator, normal_charge=50)
    await manager.async_start_manual(SERIAL, MANUAL_CHARGE, 30)

    assert await manager.async_shutdown() is True

    assert manager.manual_sessions == {}
    mock_coordinator.async_write_settings.assert_awaited_with(
        SERIAL, {"chargeCurrent": 50}
    )


@pytest.mark.asyncio
async def test_off_tariff_manager_still_puts_other_inverters_back(
    mock_hass, mock_coordinator
):
    """Another inverter left charging is restored; the manual one isn't."""
    mock_coordinator.write_target_serials = [SERIAL, "INV2"]
    mock_coordinator.data["INV2"] = {"battery": {"soc": 50}}
    manager = _make_manager(mock_hass, mock_coordinator, normal_charge=50)
    await manager.async_start_manual(SERIAL, MANUAL_CHARGE)
    manager._charging_active_serials.add("INV2")

    await manager._evaluate()

    assert manager.is_charging_active_for(SERIAL)
    assert not manager.is_charging_active_for("INV2")


@pytest.mark.asyncio
async def test_an_inverter_no_longer_a_target_is_dropped(mock_hass, mock_coordinator):
    manager = _make_manager(mock_hass, mock_coordinator)
    await manager.async_start_manual(SERIAL, MANUAL_CHARGE)

    mock_coordinator.write_target_serials = ["MASTER"]
    mock_coordinator.data["MASTER"] = {"battery": {"soc": 50}}
    await manager._evaluate()

    assert manager.manual_sessions == {}


@pytest.mark.asyncio
async def test_a_failure_for_one_inverter_is_logged(mock_hass, mock_coordinator, caplog):
    manager = _make_manager(mock_hass, mock_coordinator)
    await manager.async_start_manual(SERIAL, MANUAL_DISCHARGE)
    mock_coordinator.async_write_setting.side_effect = RuntimeError("cloud down")

    mock_coordinator.data[SERIAL]["battery"]["soc"] = 5
    await manager._evaluate()

    assert "manual mode failed for TEST123" in caplog.text


def test_the_tariff_mode_sensor_shows_it(mock_hass, mock_coordinator):
    from custom_components.sunsynk.sensor import TariffStateSensor

    manager = _make_manager(mock_hass, mock_coordinator)
    sensor = TariffStateSensor("entry", manager, MagicMock())
    assert "manual" not in sensor.extra_state_attributes

    manager._manual[SERIAL] = ManualSession(MANUAL_CHARGE)

    assert sensor.native_value == "manual_charge"
    assert sensor.extra_state_attributes["manual"] == {
        SERIAL: {"mode": "charge", "until": None}
    }


# ── The actions ────────────────────────────────────────────────────────────


@pytest.fixture
async def actions():
    handlers = {}
    hass = MagicMock()
    hass.http.async_register_static_paths = AsyncMock()
    hass.services.async_register.side_effect = (
        lambda domain, name, handler, schema, **kwargs: handlers.__setitem__(
            name, handler
        )
    )
    await integration.async_setup(hass, {})
    return hass, handlers


def _call(data):
    return SimpleNamespace(data=data, context=SimpleNamespace(user_id=None))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("service", "method", "args"),
    [
        (
            "charge_now_start",
            "async_start_manual",
            ("MASTER", MANUAL_CHARGE, 30, {"current": None, "soc": None, "power": None}),
        ),
        ("charge_now_stop", "async_stop_manual", ("MASTER", MANUAL_CHARGE, {})),
        (
            "discharge_now_start",
            "async_start_manual",
            (
                "MASTER",
                MANUAL_DISCHARGE,
                30,
                {"current": None, "soc": None, "power": None},
            ),
        ),
        ("discharge_now_stop", "async_stop_manual", ("MASTER", MANUAL_DISCHARGE, {})),
    ],
)
async def test_actions_go_to_the_tariff_manager_of_the_write_target(
    actions, service, method, args
):
    hass, handlers = actions
    coordinator = MagicMock()
    coordinator.resolve_write_target.return_value = "MASTER"
    manager = MagicMock()
    setattr(manager, method, AsyncMock())
    hass.data = {DOMAIN: {"entry": coordinator, "entry_tariff": manager}}

    with (
        patch.object(integration, "_async_ensure_control_allowed", AsyncMock()),
        patch.object(integration, "_find_coordinator", return_value=coordinator),
        patch.object(integration, "_find_entry_id_for_serial", return_value="entry"),
    ):
        await handlers[service](_call({"serial": "SLAVE", "duration": 30}))

    getattr(manager, method).assert_awaited_once_with(*args[:-1], **args[-1])


@pytest.mark.asyncio
async def test_actions_need_tariff_manager_set_up(actions):
    hass, handlers = actions
    hass.data = {DOMAIN: {"entry": MagicMock()}}

    with (
        patch.object(integration, "_async_ensure_control_allowed", AsyncMock()),
        patch.object(integration, "_find_coordinator", return_value=MagicMock()),
        patch.object(integration, "_find_entry_id_for_serial", return_value="entry"),
        pytest.raises(ServiceValidationError, match="Configure first"),
    ):
        await handlers["charge_now_start"](_call({"serial": "SN1"}))


@pytest.mark.asyncio
async def test_actions_refuse_an_unknown_inverter(actions):
    hass, handlers = actions

    with (
        patch.object(integration, "_async_ensure_control_allowed", AsyncMock()),
        patch.object(integration, "_find_coordinator", return_value=None),
        pytest.raises(ValueError, match="No Sunsynk inverter"),
    ):
        await handlers["discharge_now_stop"](_call({"serial": "NOPE"}))



# ── Current, SOC and power: the action, the options, Tariff Manager (#39) ──


@pytest.mark.asyncio
async def test_the_action_s_values_win(mock_hass, mock_coordinator):
    manager = _make_manager(mock_hass, mock_coordinator, cheap_current=100)
    manager._manual_defaults[MANUAL_CHARGE] = (60, 70, None)

    await manager.async_start_manual(SERIAL, MANUAL_CHARGE, current=40, soc=80)

    assert _written(mock_coordinator) == [("chargeCurrent", 40)]
    session = manager.manual_for(SERIAL)
    assert (session.current, session.soc, session.power) == (40, 80, None)


@pytest.mark.asyncio
async def test_the_options_come_next(mock_hass, mock_coordinator):
    manager = _make_manager(
        mock_hass, mock_coordinator, peak_discharge=100, discharge_min_soc=10
    )
    manager._manual_defaults[MANUAL_DISCHARGE] = (60, 30, 3000)

    await manager.async_start_manual(SERIAL, MANUAL_DISCHARGE)

    assert _written(mock_coordinator) == [("dischargeCurrent", 60)]
    session = manager.manual_for(SERIAL)
    assert (session.current, session.soc, session.power) == (60, 30, 3000)

    # Its own minimum: 30 %, not Tariff Manager's 10 %.
    mock_coordinator.data[SERIAL]["battery"]["soc"] = 30
    await manager._evaluate()
    assert manager.manual_for(SERIAL) is None


@pytest.mark.asyncio
async def test_a_current_from_the_options_is_enough_without_tariff_manager_s(
    mock_hass, mock_coordinator
):
    manager = _make_manager(mock_hass, mock_coordinator, cheap_current=None)
    manager._manual_defaults[MANUAL_CHARGE] = (50, None, None)

    await manager.async_start_manual(SERIAL, MANUAL_CHARGE)

    assert _written(mock_coordinator) == [("chargeCurrent", 50)]


@pytest.mark.asyncio
async def test_the_scheduler_uses_the_session_s_soc_and_power(
    mock_hass, mock_coordinator
):
    from custom_components.sunsynk.virtual_slots import VirtualSlotScheduler

    manager = _make_manager(mock_hass, mock_coordinator)
    scheduler = VirtualSlotScheduler(
        MagicMock(), mock_coordinator, "entry", SERIAL, tariff_manager=manager
    )

    await manager.async_start_manual(SERIAL, MANUAL_DISCHARGE, soc=40, power=3000)
    discharge = scheduler._resolve_override()
    assert (discharge.target_soc, discharge.sell_power) == (40, 3000)

    await manager.async_start_manual(SERIAL, MANUAL_CHARGE, soc=60)
    charge = scheduler._resolve_override()
    assert charge.target_soc == 60
    assert charge.sell_power == 0  # the rated power

    # #42: the slot power is what the inverter charges at.
    await manager.async_start_manual(SERIAL, MANUAL_CHARGE, power=4000)
    assert scheduler._resolve_override().sell_power == 4000

    await manager.async_stop_manual(SERIAL, MANUAL_CHARGE)
    manager._charging_active_serials.add(SERIAL)  # a price charge: its own values
    assert scheduler._resolve_override().target_soc == manager.target_soc


@pytest.mark.asyncio
async def test_the_action_checks_current_and_power_limits(actions):
    hass, handlers = actions
    coordinator = MagicMock()
    coordinator.resolve_write_target.return_value = "MASTER"
    manager = MagicMock(async_start_manual=AsyncMock())
    hass.data = {DOMAIN: {"entry_tariff": manager}}
    scheduler = MagicMock()
    scheduler.sell_power_limit.return_value = (5000, "rated power")

    with (
        patch.object(integration, "_async_ensure_control_allowed", AsyncMock()),
        patch.object(integration, "_find_coordinator", return_value=coordinator),
        patch.object(integration, "_find_entry_id_for_serial", return_value="entry"),
        patch.object(integration, "_find_virtual_slot_scheduler", return_value=scheduler),
        patch.object(integration, "_ensure_current_within_limit") as check,
    ):
        with pytest.raises(ServiceValidationError, match="may sell at most 5000 W"):
            await handlers["discharge_now_start"](
                _call({"serial": "INV1", "current": 80, "power": 6000})
            )
        check.assert_called_once_with(coordinator, "MASTER", "discharge", 80)

        await handlers["discharge_now_start"](
            _call({"serial": "INV1", "min_soc": 30, "power": 3000})
        )

    manager.async_start_manual.assert_awaited_once_with(
        "MASTER", MANUAL_DISCHARGE, None, current=None, soc=30, power=3000
    )


@pytest.mark.asyncio
async def test_the_charge_action_takes_a_power_within_the_rated_power(actions):
    hass, handlers = actions
    coordinator = MagicMock()
    coordinator.resolve_write_target.return_value = "MASTER"
    manager = MagicMock(async_start_manual=AsyncMock())
    hass.data = {DOMAIN: {"entry_tariff": manager}}
    scheduler = MagicMock()
    scheduler.charge_power_limit.return_value = (8000, "inverter rated 8000 W")

    with (
        patch.object(integration, "_async_ensure_control_allowed", AsyncMock()),
        patch.object(integration, "_find_coordinator", return_value=coordinator),
        patch.object(integration, "_find_entry_id_for_serial", return_value="entry"),
        patch.object(integration, "_find_virtual_slot_scheduler", return_value=scheduler),
    ):
        with pytest.raises(ServiceValidationError, match="may charge at most 8000 W"):
            await handlers["charge_now_start"](_call({"serial": "INV1", "power": 9000}))
        await handlers["charge_now_start"](_call({"serial": "INV1", "power": 3000}))

    manager.async_start_manual.assert_awaited_once_with(
        "MASTER", MANUAL_CHARGE, None, current=None, soc=None, power=3000
    )


def test_the_charge_power_limit_is_the_rated_power(mock_coordinator):
    from custom_components.sunsynk.virtual_slots import VirtualSlotScheduler

    scheduler = VirtualSlotScheduler(MagicMock(), mock_coordinator, "entry", SERIAL)
    mock_coordinator.data[SERIAL]["inverter"] = {"ratePower": 8000}
    assert scheduler.charge_power_limit() == (8000, "inverter rated 8000 W")
    mock_coordinator.data[SERIAL]["inverter"] = {}
    assert scheduler.charge_power_limit() == (None, "")

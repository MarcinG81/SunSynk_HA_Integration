"""Implausible energy counter readings stay out of the sensor state (#43)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.sunsynk.const import ALL_STATIC_SENSORS
from custom_components.sunsynk.sensor import (
    SunsynkEnergySensor,
    SunsynkSensor,
    _sunsynk_sensor,
)

SERIAL = "TEST123"
START = datetime(2026, 10, 28, 0, 0, tzinfo=UTC)


def _sensor(key, endpoint_data, endpoint="grid", rated=5000, cls=SunsynkSensor):
    description = next(d for d in ALL_STATIC_SENSORS if d.key == key)
    coordinator = MagicMock()
    coordinator.data = {
        SERIAL: {endpoint: endpoint_data, "inverter": {"ratePower": rated}}
    }
    sensor = cls(coordinator, SERIAL, description, MagicMock())
    sensor.entity_id = f"sensor.{key}"
    return sensor, coordinator


def _read(sensor, coordinator, endpoint, field, value, minutes):
    coordinator.data[SERIAL][endpoint][field] = value
    with patch(
        "custom_components.sunsynk.sensor.dt_util.utcnow",
        return_value=START + timedelta(minutes=minutes),
    ):
        return sensor.native_value


def test_swapped_halves_of_a_lifetime_total_are_ignored(caplog):
    """#43: 376.5 kWh came back as 24,674,304.5 kWh at midnight."""
    sensor, coordinator = _sensor("grid_etotal_from", {"etotalFrom": "376.0"})

    assert _read(sensor, coordinator, "grid", "etotalFrom", "376.0", 0) == 376.0
    assert _read(sensor, coordinator, "grid", "etotalFrom", "24674304.5", 5) == 376.0
    assert "ignoring 24674304.5 kWh" in caplog.text
    # The next good reading is taken as usual.
    assert _read(sensor, coordinator, "grid", "etotalFrom", "376.5", 10) == 376.5


def test_a_lifetime_total_dropping_to_zero_is_ignored():
    sensor, coordinator = _sensor("load_total_used", {"totalUsed": "1200"}, "load")

    assert _read(sensor, coordinator, "load", "totalUsed", "1200", 0) == 1200.0
    assert _read(sensor, coordinator, "load", "totalUsed", "0", 5) == 1200.0


def test_a_daily_counter_jump_is_ignored_but_its_midnight_reset_taken():
    sensor, coordinator = _sensor("grid_etoday_from", {"etodayFrom": "12.4"})

    assert _read(sensor, coordinator, "grid", "etodayFrom", "12.4", 0) == 12.4
    # #43: 394.8 kWh within five minutes.
    assert _read(sensor, coordinator, "grid", "etodayFrom", "394.8", 5) == 12.4
    # Midnight: back to 0.
    assert _read(sensor, coordinator, "grid", "etodayFrom", "0.1", 10) == 0.1


def test_a_small_dip_in_a_daily_counter_keeps_the_last_value():
    sensor, coordinator = _sensor("grid_etoday_from", {"etodayFrom": "3.3"})

    assert _read(sensor, coordinator, "grid", "etodayFrom", "3.3", 0) == 3.3
    assert _read(sensor, coordinator, "grid", "etodayFrom", "3.2", 5) == 3.3
    assert _read(sensor, coordinator, "grid", "etodayFrom", "3.4", 10) == 3.4


def test_a_rise_the_inverter_could_manage_is_taken():
    """16 kW for an hour (more than 2 × 5 kW rated) plus the 1 kWh margin."""
    sensor, coordinator = _sensor("grid_etotal_from", {"etotalFrom": "100"})

    assert _read(sensor, coordinator, "grid", "etotalFrom", "100", 0) == 100.0
    assert _read(sensor, coordinator, "grid", "etotalFrom", "116.9", 60) == 116.9


def test_an_impossible_rise_is_never_taken(caplog):
    """#43: the cloud sent the same impossible total from midnight on."""
    sensor, coordinator = _sensor("grid_etotal_from", {"etotalFrom": "376.0"})

    assert _read(sensor, coordinator, "grid", "etotalFrom", "376.0", 0) == 376.0
    for minutes in (5, 65, 600, 1440):
        assert (
            _read(sensor, coordinator, "grid", "etotalFrom", "24680858.2", minutes)
            == 376.0
        )
    assert "taking it" not in caplog.text
    # The cloud is right again.
    assert _read(sensor, coordinator, "grid", "etotalFrom", "380.1", 1445) == 380.1


def test_a_daily_counter_jump_is_held_all_day():
    """#43: 395.8 kWh imported today on an 8 kW inverter."""
    sensor, coordinator = _sensor(
        "grid_etoday_from", {"etodayFrom": "0.1"}, rated=8000
    )

    assert _read(sensor, coordinator, "grid", "etodayFrom", "0.1", 0) == 0.1
    assert _read(sensor, coordinator, "grid", "etodayFrom", "395.8", 700) == 0.1
    assert _read(sensor, coordinator, "grid", "etodayFrom", "395.8", 1430) == 0.1


def test_a_real_rise_above_the_limit_is_only_delayed():
    sensor, coordinator = _sensor("grid_etotal_from", {"etotalFrom": "100"})

    assert _read(sensor, coordinator, "grid", "etotalFrom", "100", 0) == 100.0
    # 25 kW for an hour: more than the 16 kW allowed.
    assert _read(sensor, coordinator, "grid", "etotalFrom", "125", 60) == 100.0
    # Two hours on, it fits.
    assert _read(sensor, coordinator, "grid", "etotalFrom", "125", 120) == 125.0


def test_a_parallel_group_allows_its_inverters_together():
    sensor, coordinator = _sensor(
        "grid_etotal_from", {"etotalFrom": "100"}, rated=10000
    )
    coordinator.serials = [SERIAL, "SLAVE"]
    coordinator.resolve_write_target.return_value = SERIAL

    assert _read(sensor, coordinator, "grid", "etotalFrom", "100", 0) == 100.0
    # 2 × 10 kW × 2 inverters.
    assert _read(sensor, coordinator, "grid", "etotalFrom", "140", 60) == 140.0


def test_a_bigger_inverter_allows_a_faster_rise():
    sensor, coordinator = _sensor(
        "grid_etotal_from", {"etotalFrom": "100"}, rated="nan"
    )
    assert _read(sensor, coordinator, "grid", "etotalFrom", "100", 0) == 100.0
    assert _read(sensor, coordinator, "grid", "etotalFrom", "160", 60) == 100.0

    sensor, coordinator = _sensor(
        "grid_etotal_from", {"etotalFrom": "100"}, rated=30000
    )
    assert _read(sensor, coordinator, "grid", "etotalFrom", "100", 0) == 100.0
    assert _read(sensor, coordinator, "grid", "etotalFrom", "160", 60) == 160.0


def test_a_value_reported_for_over_an_hour_is_taken(caplog):
    """A counter that really was reset, e.g. after a replacement."""
    sensor, coordinator = _sensor("grid_etotal_from", {"etotalFrom": "376"})

    assert _read(sensor, coordinator, "grid", "etotalFrom", "376", 0) == 376.0
    assert _read(sensor, coordinator, "grid", "etotalFrom", "2", 5) == 376.0
    assert _read(sensor, coordinator, "grid", "etotalFrom", "2.1", 30) == 376.0
    assert _read(sensor, coordinator, "grid", "etotalFrom", "2.2", 66) == 2.2
    assert "for over an hour, taking it" in caplog.text


@pytest.mark.parametrize("value", [None, "--"])
def test_a_missing_reading_isnt_guarded(value):
    sensor, coordinator = _sensor("grid_etotal_from", {"etotalFrom": "376"})

    assert _read(sensor, coordinator, "grid", "etotalFrom", value, 0) is None


def test_other_sensors_arent_guarded():
    sensor, coordinator = _sensor("grid_pac", {"pac": "100"})

    assert sensor._guard is None


def test_an_unknown_rated_power_uses_16_kw():
    sensor, coordinator = _sensor("grid_etotal_from", {"etotalFrom": "100"}, rated=None)

    assert _read(sensor, coordinator, "grid", "etotalFrom", "100", 0) == 100.0
    assert _read(sensor, coordinator, "grid", "etotalFrom", "116", 60) == 116.0
    assert _read(sensor, coordinator, "grid", "etotalFrom", "300", 120) == 116.0


def test_only_energy_counters_restore_their_value():
    coordinator = MagicMock()
    by_key = {d.key: d for d in ALL_STATIC_SENSORS}

    counter = _sunsynk_sensor(coordinator, SERIAL, by_key["grid_etotal_from"], {})
    power = _sunsynk_sensor(coordinator, SERIAL, by_key["grid_pac"], {})

    assert isinstance(counter, SunsynkEnergySensor)
    assert not isinstance(power, SunsynkEnergySensor)


async def _added(sensor, native_value, last_updated=START):
    data = None if native_value is None else MagicMock(native_value=native_value)
    state = MagicMock(last_updated=last_updated)
    with (
        patch(
            "homeassistant.helpers.update_coordinator.CoordinatorEntity."
            "async_added_to_hass",
            AsyncMock(),
        ),
        patch.object(sensor, "async_get_last_sensor_data", AsyncMock(return_value=data)),
        patch.object(sensor, "async_get_last_state", AsyncMock(return_value=state)),
    ):
        await sensor.async_added_to_hass()


@pytest.mark.asyncio
async def test_a_restart_while_the_cloud_is_wrong_keeps_the_last_good_value():
    sensor, coordinator = _sensor(
        "grid_etotal_from", {"etotalFrom": "376"}, cls=SunsynkEnergySensor
    )
    await _added(sensor, 376.0)

    assert _read(sensor, coordinator, "grid", "etotalFrom", "24680858.2", 5) == 376.0
    assert _read(sensor, coordinator, "grid", "etotalFrom", "376.4", 10) == 376.4


@pytest.mark.asyncio
@pytest.mark.parametrize("restored", [None, "unknown", float("nan")])
async def test_nothing_to_restore_takes_the_first_reading(restored):
    sensor, coordinator = _sensor(
        "grid_etotal_from", {"etotalFrom": "376"}, cls=SunsynkEnergySensor
    )
    await _added(sensor, restored)

    assert _read(sensor, coordinator, "grid", "etotalFrom", "377", 5) == 377.0

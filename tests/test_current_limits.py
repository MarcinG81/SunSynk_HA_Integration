"""Battery current limits: what the inverter and the battery will actually use."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.sunsynk.current_limits import (
    CurrentLimit,
    battery_safe_current,
    current_limit,
    describe_limit,
    group_sizes,
)


def _data(setting=None, rated=None, live=None):
    return {
        "settings": {"batteryMaxCurrentDischarge": setting},
        "battery": {
            "maxDischargeCurrentLimit": rated,
            "dischargeCurrentLimit": live,
        },
    }


def test_lower_of_inverter_and_battery_wins():
    limit = current_limit({"A": _data(setting="120", rated=100)}, ["A"], "discharge")

    assert limit == CurrentLimit(inverter=120, battery=100, battery_total=100)
    assert limit.maximum == 100
    assert describe_limit(limit) == "inverter 120 A, battery BMS 100 A"


def test_most_restrictive_inverter_sets_the_limit():
    data = {"A": _data(setting=240), "B": _data(setting=150)}

    assert current_limit(data, ["A", "B"], "discharge").maximum == 150


@pytest.mark.parametrize("bad", [None, "", "bad", 0, -5, float("nan"), float("inf")])
def test_implausible_readings_are_not_limits(bad):
    limit = current_limit({"A": _data(setting=bad, rated=bad)}, ["A"], "discharge")

    assert limit.maximum is None
    assert describe_limit(limit) == ""


def test_inverter_limit_above_its_range_is_unknown_but_a_bank_limit_is_not():
    """A whole bank's BMS may allow 1040 A (#21); one inverter can't."""
    limit = current_limit({"A": _data(setting=1040, rated=1040)}, ["A"], "discharge")

    assert limit == CurrentLimit(inverter=None, battery=1040, battery_total=1040)


def test_parallel_group_shares_the_bms_limit():
    """#21: two 12 kW inverters at 240 A each on one 1256 Ah bank."""
    data = {"M": _data(setting=240, rated=400)}

    limit = current_limit(data, ["M"], "discharge", {"M": 2})

    assert limit == CurrentLimit(240, 200, battery_total=400, shared_by=2)
    assert limit.maximum == 200
    assert describe_limit(limit) == (
        "inverter 240 A, battery BMS 400 A shared by 2 inverters = 200 A each"
    )


def test_live_bms_limit_counts_only_when_asked():
    data = {"A": _data(rated=0, live=192)}

    assert current_limit(data, ["A"], "discharge").battery is None
    assert current_limit(data, ["A"], "discharge", live=True).battery == 192


def test_charge_reads_the_charge_fields():
    data = {
        "A": {
            "settings": {"batteryMaxCurrentCharge": 90},
            "battery": {"maxChargeCurrentLimit": "110.0"},
        }
    }

    assert current_limit(data, ["A"], "charge") == CurrentLimit(
        inverter=90, battery=110, battery_total=110
    )


def test_missing_inverter_data_is_unknown():
    assert current_limit(None, ["A"], "charge").maximum is None


def _coordinator(data, routes):
    return SimpleNamespace(
        data=data,
        serials=list(routes),
        resolve_write_target=routes.__getitem__,
    )


def test_group_sizes_count_each_master_and_its_slaves():
    coordinator = _coordinator({}, {"S": "M", "M": "M", "X": "X"})

    assert group_sizes(coordinator) == {"M": 2, "X": 1}


def test_group_sizes_ignore_unresolvable_targets():
    coordinator = SimpleNamespace(serials=["A"], resolve_write_target=lambda s: None)

    assert group_sizes(coordinator) == {}


@pytest.mark.parametrize(
    ("amps", "expected"),
    [
        (240, 240),  # 2 x 240 = 480 A, the BMS allows 1040 A
        (600, 520),  # 2 x 600 would be 1200 A: lowered to 1040 / 2
        (0, 0),  # stopping is never changed
    ],
)
def test_parallel_discharge_is_lowered_to_the_bank_limit(amps, expected):
    coordinator = _coordinator({"M": _data(live=1040)}, {"S": "M", "M": "M"})

    assert battery_safe_current(coordinator, "M", "discharge", amps) == expected


def test_parallel_charge_uses_the_live_bms_limit():
    """#21: the BMS allowed 192 A near full; 2 x 120 A is lowered to 96 A."""
    coordinator = _coordinator(
        {"M": {"battery": {"maxChargeCurrentLimit": 0, "chargeCurrentLimit": 192}}},
        {"S": "M", "M": "M"},
    )

    assert battery_safe_current(coordinator, "M", "charge", 120) == 96


def test_no_bms_limit_leaves_the_current_alone():
    coordinator = _coordinator({"M": {"battery": {}}}, {"M": "M"})

    assert battery_safe_current(coordinator, "M", "charge", 250) == 250

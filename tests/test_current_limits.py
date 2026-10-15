"""Battery current limits: what the inverter and the battery will actually use."""

from __future__ import annotations

import pytest

from custom_components.sunsynk.current_limits import (
    CurrentLimit,
    current_limit,
    describe_limit,
)


def _data(setting=None, bms=None):
    return {
        "settings": {"batteryMaxCurrentDischarge": setting},
        "battery": {"maxDischargeCurrentLimit": bms},
    }


def test_lower_of_inverter_and_battery_wins():
    limit = current_limit({"A": _data(setting="120", bms=100)}, ["A"], "discharge")

    assert limit == CurrentLimit(inverter=120, battery=100)
    assert limit.maximum == 100
    assert describe_limit(limit) == "inverter 120 A, battery BMS 100 A"


def test_most_restrictive_inverter_sets_the_limit():
    data = {"A": _data(setting=240), "B": _data(setting=150)}

    assert current_limit(data, ["A", "B"], "discharge").maximum == 150


@pytest.mark.parametrize("bad", [None, "", "bad", 0, -5, 1040, float("nan"), float("inf")])
def test_implausible_readings_are_not_limits(bad):
    """A parallel master reported 1040 A (#21); that is not a limit."""
    limit = current_limit({"A": _data(setting=bad, bms=bad)}, ["A"], "discharge")

    assert limit.maximum is None
    assert describe_limit(limit) == ""


def test_charge_reads_the_charge_fields():
    data = {
        "A": {
            "settings": {"batteryMaxCurrentCharge": 90},
            "battery": {"maxChargeCurrentLimit": "110.0"},
        }
    }

    assert current_limit(data, ["A"], "charge") == CurrentLimit(inverter=90, battery=110)


def test_missing_inverter_data_is_unknown():
    assert current_limit(None, ["A"], "charge").maximum is None

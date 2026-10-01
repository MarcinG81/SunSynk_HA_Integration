"""Tests for the central inverter-write validation boundary."""
from __future__ import annotations

import math

import pytest

from custom_components.sunsynk.write_validation import (
    MAX_CURRENT_A,
    MAX_PLANT_PRICE,
    MAX_POWER_W,
    SunsynkSettingValidationError,
    validate_plant_price,
    validate_setting_batch,
    validate_setting_value,
)


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    [
        ("chargeCurrent", 0, 0),
        ("dischargeCurrent", str(MAX_CURRENT_A), MAX_CURRENT_A),
        ("cap1", 100.0, 100),
        ("sellTime6Pac", MAX_POWER_W, MAX_POWER_W),
        ("sysWorkMode", 4, 4),
        ("time1on", "true", 1),
        ("sellTime1on", False, 0),
        ("sellTime1", "23:59", "23:59"),
    ],
)
def test_valid_values_are_normalized(key, value, expected):
    assert validate_setting_value(key, value) == expected


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("chargeCurrent", -1),
        ("chargeCurrent", MAX_CURRENT_A + 1),
        ("chargeCurrent", 1.5),
        ("chargeCurrent", math.nan),
        ("chargeCurrent", math.inf),
        ("cap1", 101),
        ("sellTime1Pac", MAX_POWER_W + 1),
        ("sysWorkMode", 5),
        ("time1on", 2),
        ("sellTime1", "24:00"),
        ("sellTime1", "8:00"),
        ("notARealSetting", 1),
    ],
)
def test_invalid_values_are_rejected(key, value):
    with pytest.raises(SunsynkSettingValidationError):
        validate_setting_value(key, value)


@pytest.mark.parametrize("value", [True, object()])
def test_integer_validation_rejects_boolean_and_non_numeric(value):
    with pytest.raises(SunsynkSettingValidationError):
        validate_setting_value("chargeCurrent", value)


@pytest.mark.parametrize(("value", "expected"), [("no", 0), ("off", 0)])
def test_boolean_string_false_variants(value, expected):
    assert validate_setting_value("time1on", value) == expected


def test_batch_validation_does_not_mutate_input_on_failure():
    original = {"cap1": 80, "sellTime1Pac": MAX_POWER_W + 1}

    with pytest.raises(SunsynkSettingValidationError):
        validate_setting_batch(original)

    assert original == {"cap1": 80, "sellTime1Pac": MAX_POWER_W + 1}


@pytest.mark.parametrize("value", [0, "0.25", MAX_PLANT_PRICE])
def test_valid_plant_prices(value):
    assert validate_plant_price(value) == float(value)


@pytest.mark.parametrize(
    "value", [-0.01, MAX_PLANT_PRICE + 0.01, math.nan, math.inf, True, "bad"]
)
def test_invalid_plant_prices(value):
    with pytest.raises(SunsynkSettingValidationError):
        validate_plant_price(value)

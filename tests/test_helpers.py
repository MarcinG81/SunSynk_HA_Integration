"""Tests for build_device_info (custom_components/sunsynk/helpers.py)."""
from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.sunsynk.helpers import build_device_info


def _coordinator_with(inverter_data: dict) -> MagicMock:
    coordinator = MagicMock()
    coordinator.data = {"SN1": {"inverter": inverter_data}}
    return coordinator


def test_uses_alias_as_name_when_present():
    info = build_device_info(_coordinator_with({"alias": "My Inverter"}), "SN1")
    assert info["name"] == "My Inverter"


def test_falls_back_to_serial_based_name_when_no_alias():
    info = build_device_info(_coordinator_with({}), "SN1")
    assert info["name"] == "Sunsynk SN1"


def test_uses_reported_brand():
    info = build_device_info(_coordinator_with({"brand": "Deye"}), "SN1")
    assert info["manufacturer"] == "Deye"


def test_falls_back_to_sunsynk_brand_when_missing():
    info = build_device_info(_coordinator_with({}), "SN1")
    assert info["manufacturer"] == "Sunsynk"


def test_falls_back_to_sunsynk_brand_when_not_a_string():
    info = build_device_info(_coordinator_with({"brand": 123}), "SN1")
    assert info["manufacturer"] == "Sunsynk"


def test_model_uses_model_value_helper():
    info = build_device_info(_coordinator_with({"model": "SUN-5K"}), "SN1")
    assert info["model"] == "SUN-5K"


def test_model_falls_back_when_no_data_available():
    info = build_device_info(_coordinator_with({}), "SN1")
    assert info["model"] == "Sunsynk Inverter"


def test_sw_version_from_nested_version_dict():
    info = build_device_info(
        _coordinator_with({"version": {"masterVer": "1.2.3"}}), "SN1"
    )
    assert info["sw_version"] == "1.2.3"


def test_sw_version_none_when_version_missing():
    info = build_device_info(_coordinator_with({}), "SN1")
    assert info["sw_version"] is None


def test_identifiers_and_serial_number_use_the_given_serial():
    info = build_device_info(_coordinator_with({}), "SN42")
    assert info["serial_number"] == "SN42"
    assert ("sunsynk", "SN42") in info["identifiers"]


def test_handles_missing_serial_in_coordinator_data_gracefully():
    coordinator = MagicMock()
    coordinator.data = {}
    info = build_device_info(coordinator, "UNKNOWN")
    assert info["name"] == "Sunsynk UNKNOWN"


def test_handles_none_coordinator_data_gracefully():
    coordinator = MagicMock()
    coordinator.data = None
    info = build_device_info(coordinator, "SN1")
    assert info["name"] == "Sunsynk SN1"

"""Tests for TariffPriceQualitySensor (custom_components/sunsynk/sensor.py).

Regression coverage for #21: with a separate export price entity configured,
the sensor's main state used to always reflect the import side only — a
perfectly healthy import price hid a stale/missing export sensor silently
blocking discharging, with nothing in the visible state pointing at why.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.sunsynk.sensor import TariffPriceQualitySensor

_DEVICE_INFO = MagicMock()


def _sensor(price_quality: str, export_price_quality: str) -> TariffPriceQualitySensor:
    manager = MagicMock()
    manager.price_quality = price_quality
    manager.export_price_quality = export_price_quality
    return TariffPriceQualitySensor("entry1", manager, _DEVICE_INFO)


def test_shows_ok_when_both_sides_are_ok():
    assert _sensor("ok", "ok").native_value == "ok"


def test_shows_import_problem_when_import_is_bad():
    assert _sensor("stale", "ok").native_value == "stale"


def test_surfaces_export_problem_even_though_import_is_ok():
    """The core of #21: import (Octopus) healthy, export (a helper) stale
    — the sensor must show "stale", not silently report "ok"."""
    assert _sensor("ok", "stale").native_value == "stale"


def test_prefers_import_problem_when_both_are_bad():
    assert _sensor("not_found", "stale").native_value == "not_found"

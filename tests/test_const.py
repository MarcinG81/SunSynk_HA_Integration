"""Tests for computed sensor value functions in const.py."""
from __future__ import annotations

from custom_components.sunsynk.const import _battery_soh_value


def test_soh_none_when_fields_missing():
    assert _battery_soh_value({}) is None


def test_soh_none_when_etotal_dischg_zero():
    assert _battery_soh_value({"etotalChg": 100.0, "etotalDischg": 0}) is None


def test_soh_matches_a_real_aged_battery():
    """Real report (#14): a battery a few years old with measurable wear
    read 95.0% via this exact formula on a sibling project, and 100%
    (i.e. no signal at all) via the correctCap/capacity formula this
    integration used briefly instead — which is why it was reverted.
    """
    etotal_dischg = 1000.0
    etotal_chg = etotal_dischg * 1.05  # engineered to land on 95.0%
    assert _battery_soh_value({"etotalChg": etotal_chg, "etotalDischg": etotal_dischg}) == 95.0


def test_soh_clamped_to_100_when_counters_diverge_above_it():
    """Real report (#14/#15): a dump with etotalDischg 807.9 kWh higher
    than etotalChg produced 139.3% unclamped — physically meaningless for
    a percentage. Must be capped rather than displayed raw.
    """
    result = _battery_soh_value({"etotalChg": 100.0, "etotalDischg": 807.9})
    assert result == 100.0


def test_soh_clamped_to_0_when_negative():
    result = _battery_soh_value({"etotalChg": 300.0, "etotalDischg": 100.0})
    assert result == 0.0


def test_soh_trivial_case_both_equal():
    assert _battery_soh_value({"etotalChg": 500.0, "etotalDischg": 500.0}) == 100.0


def test_soh_none_on_non_numeric_input():
    assert _battery_soh_value({"etotalChg": "--", "etotalDischg": 500.0}) is None

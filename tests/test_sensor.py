"""Tests for dynamic sensor discovery in sensor.py.

Covers the generator/micro-inverter power gating added for #17: these
sensors only exist behind the /flow endpoint's existsGen/existsMin flags,
since most accounts have neither a generator nor a micro-inverter wired
into that port and the sensor would otherwise sit permanently unavailable.
"""
from __future__ import annotations

from types import SimpleNamespace

from custom_components.sunsynk.sensor import _build_dynamic_descriptions


def _coordinator(flow: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(data={"TEST123": {"flow": flow or {}}})


def test_generator_power_sensor_added_when_exists_gen_true():
    descriptions = _build_dynamic_descriptions(_coordinator({"existsGen": True}), "TEST123")
    matches = [d for d in descriptions if d.key == "generator_power"]
    assert len(matches) == 1
    assert matches[0].endpoint == "flow"
    assert matches[0].data_key == "genPower"


def test_micro_inverter_power_sensor_added_when_exists_min_true():
    descriptions = _build_dynamic_descriptions(_coordinator({"existsMin": True}), "TEST123")
    matches = [d for d in descriptions if d.key == "micro_inverter_power"]
    assert len(matches) == 1
    assert matches[0].endpoint == "flow"
    assert matches[0].data_key == "minPower"


def test_both_generator_and_micro_inverter_sensors_added_when_both_flags_true():
    """A micro-inverter physically wired into the generator port (#17) may
    surface under either flag depending on how Sunsynk classifies it — both
    must be able to coexist rather than one suppressing the other.
    """
    descriptions = _build_dynamic_descriptions(
        _coordinator({"existsGen": True, "existsMin": True}), "TEST123"
    )
    keys = {d.key for d in descriptions}
    assert "generator_power" in keys
    assert "micro_inverter_power" in keys


def test_no_generator_or_micro_inverter_sensors_when_flags_absent():
    descriptions = _build_dynamic_descriptions(_coordinator({}), "TEST123")
    keys = {d.key for d in descriptions}
    assert "generator_power" not in keys
    assert "micro_inverter_power" not in keys


def test_no_generator_or_micro_inverter_sensors_when_flags_false():
    descriptions = _build_dynamic_descriptions(
        _coordinator({"existsGen": False, "existsMin": False}), "TEST123"
    )
    keys = {d.key for d in descriptions}
    assert "generator_power" not in keys
    assert "micro_inverter_power" not in keys

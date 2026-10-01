"""Tests for HH:MM validation on Sunsynk time-slot values.

#21 added a hard :00/:30-only restriction after one reporter's Sunsynk
portal test rejected 22:45. #25 showed that was too strict: a reporter's
real-world use of Predbat — writing 5-minute-granularity schedules
straight through the same settings-write API this integration uses —
confirmed their inverter accepts and executes non-:00/:30 values without
issue. The portal's dropdown turned out to be a UI convention, not a
universal API/firmware constraint, and apparently varies by inverter
model/firmware. Validation here only enforces a well-formed HH:MM now.
"""
from __future__ import annotations

import pytest
import voluptuous as vol

from custom_components.sunsynk import _SERVICE_SET_VIRTUAL_SLOT_SCHEMA
from custom_components.sunsynk.text import _TIME_PATTERN


class TestTextEntityTimePattern:
    @pytest.mark.parametrize(
        "value", ["00:00", "09:30", "13:00", "23:30", "22:45", "09:15", "13:01", "00:59"]
    )
    def test_accepts_any_well_formed_hh_mm(self, value):
        assert _TIME_PATTERN.match(value) is not None

    @pytest.mark.parametrize("value", ["24:00", "12:60", "9:30", "12:5", "noon", ""])
    def test_rejects_malformed_values(self, value):
        assert _TIME_PATTERN.match(value) is None


class TestSetVirtualSlotServiceSchema:
    def _base_call(self, **overrides):
        data = {
            "serial": "TEST123",
            "slot_id": 1,
            "start": "20:30",
            "end": "21:00",
            "mode": "discharge",
        }
        data.update(overrides)
        return data

    def test_accepts_half_hour_boundaries(self):
        result = _SERVICE_SET_VIRTUAL_SLOT_SCHEMA(self._base_call())
        assert result["start"] == "20:30"
        assert result["end"] == "21:00"

    def test_accepts_non_half_hour_boundaries(self):
        """The real-world case from #25: a reporter's Predbat-driven
        5-minute schedules are genuinely accepted and executed by their
        inverter — this integration shouldn't block that up front."""
        result = _SERVICE_SET_VIRTUAL_SLOT_SCHEMA(self._base_call(start="22:45", end="22:50"))
        assert result["start"] == "22:45"
        assert result["end"] == "22:50"

    def test_rejects_malformed_start(self):
        with pytest.raises(vol.Invalid):
            _SERVICE_SET_VIRTUAL_SLOT_SCHEMA(self._base_call(start="9:5"))

    def test_rejects_malformed_end(self):
        with pytest.raises(vol.Invalid):
            _SERVICE_SET_VIRTUAL_SLOT_SCHEMA(self._base_call(end="24:00"))

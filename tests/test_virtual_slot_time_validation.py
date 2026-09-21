"""Tests for the 30-minute-boundary constraint on Sunsynk time-slot values.

Regression coverage for #21: Sunsynk's System Mode Timer only accepts
:00/:30 minute values — confirmed both via the Sunsynk portal itself
(silently rejects e.g. 22:45) and chattersley/sunsynk-home-assistant's own
docs. A finer-grained value isn't rejected by the settings-write API, it's
just silently ignored by the inverter — exactly the kind of "looks
successful, does nothing" failure mode this project treats as a bug to
validate against up front rather than a footgun to leave for users.
"""
from __future__ import annotations

import pytest
import voluptuous as vol

from custom_components.sunsynk import _SERVICE_SET_VIRTUAL_SLOT_SCHEMA
from custom_components.sunsynk.text import _TIME_PATTERN


class TestTextEntityTimePattern:
    @pytest.mark.parametrize("value", ["00:00", "09:30", "13:00", "23:30"])
    def test_accepts_half_hour_boundaries(self, value):
        assert _TIME_PATTERN.match(value) is not None

    @pytest.mark.parametrize("value", ["22:45", "09:15", "13:01", "00:59"])
    def test_rejects_non_half_hour_values(self, value):
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

    def test_rejects_start_not_on_half_hour_boundary(self):
        """The exact real-world case from #21: the reporter's portal
        rejected 22:45/22:50, and this integration hadn't been validating
        that at all before writing it — a slot could be "saved" here and
        silently never actually apply on the real inverter."""
        with pytest.raises(vol.Invalid):
            _SERVICE_SET_VIRTUAL_SLOT_SCHEMA(self._base_call(start="22:45"))

    def test_rejects_end_not_on_half_hour_boundary(self):
        with pytest.raises(vol.Invalid):
            _SERVICE_SET_VIRTUAL_SLOT_SCHEMA(self._base_call(end="22:50"))

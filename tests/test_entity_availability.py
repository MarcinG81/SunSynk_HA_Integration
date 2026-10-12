"""Writable entities are unavailable for settings the inverter doesn't have (#33)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.sunsynk.number import WRITABLE_NUMBERS, SunsynkNumberEntity
from custom_components.sunsynk.switch import WRITABLE_SWITCHES, SunsynkSwitchEntity
from custom_components.sunsynk.text import WRITABLE_TEXTS, SunsynkTextEntity


def _coordinator(settings):
    coordinator = MagicMock()
    coordinator.last_update_success = True
    coordinator.data = {"SN1": {"settings": settings}}
    return coordinator


def _sell_time1_switch():
    return next(d for d in WRITABLE_SWITCHES if d.setting_key == "sellTime1on")


@pytest.mark.parametrize(
    ("entity_cls", "description"),
    [
        (SunsynkSwitchEntity, _sell_time1_switch()),
        (SunsynkNumberEntity, WRITABLE_NUMBERS[0]),
        (SunsynkTextEntity, WRITABLE_TEXTS[0]),
    ],
)
def test_available_only_when_the_inverter_reports_the_setting(entity_cls, description):
    present = entity_cls(
        _coordinator({description.setting_key: "1"}), "SN1", description, MagicMock()
    )
    absent = entity_cls(_coordinator({"other": "1"}), "SN1", description, MagicMock())

    assert present.available is True
    assert absent.available is False


def test_unavailable_while_the_coordinator_is_failing():
    description = _sell_time1_switch()
    coordinator = _coordinator({"sellTime1on": "false"})
    coordinator.last_update_success = False
    switch = SunsynkSwitchEntity(coordinator, "SN1", description, MagicMock())

    assert switch.available is False

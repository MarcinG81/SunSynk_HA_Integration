"""Tariff Manager settings changed live survive a restart (#30)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.sunsynk import _async_update_listener
from custom_components.sunsynk.const import DOMAIN
from custom_components.sunsynk.helpers import (
    options_snapshot_key,
    persist_runtime_option,
)
from custom_components.sunsynk.number import TARIFF_NUMBERS, TariffNumberEntity
from custom_components.sunsynk.switch import TariffManagerSwitch


def _hass_and_entry(options=None):
    entry = SimpleNamespace(entry_id="entry", options=dict(options or {}))
    hass = SimpleNamespace(
        data={DOMAIN: {options_snapshot_key("entry"): dict(entry.options)}},
        config_entries=MagicMock(async_reload=AsyncMock()),
    )

    def _update_entry(target, *, options):
        target.options = options

    hass.config_entries.async_update_entry.side_effect = _update_entry
    return hass, entry


def test_persist_runtime_option_saves_and_updates_snapshot():
    hass, entry = _hass_and_entry({"cheap_threshold": 0.1})

    persist_runtime_option(hass, entry, "cheap_threshold", 0.12)

    assert entry.options == {"cheap_threshold": 0.12}
    assert hass.data[DOMAIN][options_snapshot_key("entry")] == entry.options


def test_persist_runtime_option_skips_unchanged_value():
    hass, entry = _hass_and_entry({"tariff_enabled": True})

    persist_runtime_option(hass, entry, "tariff_enabled", True)

    hass.config_entries.async_update_entry.assert_not_called()


@pytest.mark.asyncio
async def test_live_change_does_not_reload_the_integration():
    hass, entry = _hass_and_entry({"cheap_threshold": 0.1})

    persist_runtime_option(hass, entry, "cheap_threshold", 0.12)
    await _async_update_listener(hass, entry)

    hass.config_entries.async_reload.assert_not_awaited()


@pytest.mark.asyncio
async def test_configure_change_still_reloads_the_integration():
    hass, entry = _hass_and_entry({"cheap_threshold": 0.1})

    entry.options = {"cheap_threshold": 0.2}  # saved through Configure
    await _async_update_listener(hass, entry)

    hass.config_entries.async_reload.assert_awaited_once_with("entry")


@pytest.mark.asyncio
async def test_tariff_number_persists_its_option_key():
    hass, entry = _hass_and_entry()
    manager = MagicMock()
    target_soc = next(d for d in TARIFF_NUMBERS if d.key == "target_soc")
    number = TariffNumberEntity("entry", manager, target_soc, MagicMock(), entry)
    number.hass = hass

    await number.async_set_native_value(85.0)

    manager.set_target_soc.assert_called_once_with(85)
    # Saved under the same key the Configure form uses.
    assert entry.options == {"cheap_target_soc": 85}
    assert isinstance(entry.options["cheap_target_soc"], int)


@pytest.mark.asyncio
async def test_tariff_threshold_keeps_its_decimal_value():
    hass, entry = _hass_and_entry()
    manager = MagicMock()
    threshold = next(d for d in TARIFF_NUMBERS if d.key == "cheap_threshold")
    number = TariffNumberEntity("entry", manager, threshold, MagicMock(), entry)
    number.hass = hass

    await number.async_set_native_value(0.125)

    assert entry.options == {"cheap_threshold": 0.125}


@pytest.mark.asyncio
async def test_tariff_switch_persists_its_state():
    hass, entry = _hass_and_entry()
    manager = MagicMock()
    switch = TariffManagerSwitch("entry", manager, MagicMock(), entry)
    switch.hass = hass

    await switch.async_turn_on()
    assert entry.options == {"tariff_enabled": True}
    await switch.async_turn_off()
    assert entry.options == {"tariff_enabled": False}
    assert [c.args for c in manager.set_enabled.call_args_list] == [(True,), (False,)]

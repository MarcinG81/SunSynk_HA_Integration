"""Tariff Manager switch in a real Home Assistant instance (#21).

A tester on beta.9 reported the switch flipping back off within ~2 s with
the mode still "disabled". Unit tests use lightweight doubles, so this
drives the real entity, service call, options update and listener path.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sunsynk.const import DOMAIN

SERIAL = "2406060181"


@pytest.fixture(autouse=True)
def _custom_integrations(enable_custom_integrations):
    yield


def _inverter_data() -> dict:
    return {
        "inverter": {"sn": SERIAL, "alias": "Master", "ratePower": 8000},
        "battery": {"soc": 25},
        "settings": {
            "sn": SERIAL,
            "chargeCurrent": 50,
            "dischargeCurrent": 50,
            "parallel": 0,
            "equipMode": 1,
        },
    }


async def _setup(hass, options):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="user@example.com",
        unique_id="api.sunsynk.net_user@example.com",
        data={
            "api_server": "api.sunsynk.net",
            "username": "user@example.com",
            "password": "secret",
            "serials": [SERIAL],
        },
        options=options,
    )
    entry.add_to_hass(hass)
    # The frontend package isn't installed in the test environment.
    hass.config.components.update({"frontend", "lovelace", "http"})
    hass.states.async_set("sensor.import_price", "0.26")
    hass.states.async_set("input_number.export_rate", "0.15")
    with (
        patch(
            "custom_components.sunsynk.api.auth.SunsynkAuth.async_get_token",
            AsyncMock(return_value="token"),
        ),
        patch(
            "custom_components.sunsynk.api.client.SunsynkClient.async_fetch_all",
            AsyncMock(side_effect=lambda session, serial: _inverter_data()),
        ),
        patch(
            "custom_components.sunsynk.api.client.SunsynkClient.async_get_settings",
            AsyncMock(side_effect=lambda session, serial: _inverter_data()["settings"]),
        ),
        patch(
            "custom_components.sunsynk.api.client.SunsynkClient.async_write_settings",
            AsyncMock(),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


PAY4PROPERTY_OPTIONS = {
    "serials": [SERIAL],
    "price_entity": "sensor.import_price",
    "export_price_entity": "input_number.export_rate",
    "cheap_threshold": 0.1,
    "cheap_charge_current": 80,
    "normal_charge_current": 40,
    "expensive_threshold": 0.16,
    "peak_discharge_current": 27,
    "normal_discharge_current": 20,
    "discharge_min_soc": 10,
    "tariff_start_hour": 0,
    "tariff_end_hour": 0,
    "price_max_age": 90,
}


@pytest.mark.asyncio
async def test_tariff_manager_switch_stays_on(hass):
    entry = await _setup(hass, dict(PAY4PROPERTY_OPTIONS))
    registry = er.async_get(hass)
    switch = registry.async_get_entity_id("switch", DOMAIN, f"{entry.entry_id}_tariff_enabled")
    mode = registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_tariff_mode")
    assert hass.states.get(switch).state == "off"

    await hass.services.async_call("switch", "turn_on", {"entity_id": switch}, blocking=True)
    await hass.async_block_till_done()

    assert hass.states.get(switch).state == "on"
    assert hass.states.get(mode).state == "idle"
    assert "decisions" in hass.states.get(mode).attributes


SLAVE = "2406060175"


def _parallel_data(serial: str) -> dict:
    role = 1 if serial == SERIAL else 0
    return {
        "inverter": {"sn": serial, "alias": f"{serial} {'Master' if role else 'Slave'}",
                     "ratePower": 8000},
        "battery": {"soc": 25},
        "settings": {
            "sn": serial,
            "chargeCurrent": 50,
            "dischargeCurrent": 50,
            "parallel": 1,
            "equipMode": role,
            "sellTime1on": False,
            "sellTime6on": False,
            "time1on": False,
            "time6on": False,
        },
    }


async def _setup_parallel(hass, options):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="user@example.com",
        unique_id="api.sunsynk.net_user@example.com",
        data={
            "api_server": "api.sunsynk.net",
            "username": "user@example.com",
            "password": "secret",
            "serials": [SLAVE, SERIAL],
        },
        options=options,
    )
    entry.add_to_hass(hass)
    hass.config.components.update({"frontend", "lovelace", "http"})
    hass.states.async_set("sensor.import_price", "26.38")
    hass.states.async_set("input_number.export_rate", "0.15")
    patches = (
        patch(
            "custom_components.sunsynk.api.auth.SunsynkAuth.async_get_token",
            AsyncMock(return_value="token"),
        ),
        patch(
            "custom_components.sunsynk.api.client.SunsynkClient.async_fetch_all",
            AsyncMock(side_effect=lambda session, serial: _parallel_data(serial)),
        ),
        patch(
            "custom_components.sunsynk.api.client.SunsynkClient.async_get_settings",
            AsyncMock(side_effect=lambda session, serial: _parallel_data(serial)["settings"]),
        ),
        patch(
            "custom_components.sunsynk.api.client.SunsynkClient.async_write_settings",
            AsyncMock(),
        ),
        patch(
            "custom_components.sunsynk.api.client.SunsynkClient.async_get_plant_info",
            AsyncMock(return_value={}),
        ),
    )
    for p in patches:
        p.start()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, patches


@pytest.mark.asyncio
async def test_tariff_switch_on_parallel_system_after_being_switched_off(hass):
    options = {**PAY4PROPERTY_OPTIONS, "serials": [SLAVE, SERIAL], "tariff_enabled": False}
    entry, patches = await _setup_parallel(hass, options)
    try:
        registry = er.async_get(hass)
        switch = registry.async_get_entity_id(
            "switch", DOMAIN, f"{entry.entry_id}_tariff_enabled"
        )
        mode = registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_tariff_mode")

        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": switch}, blocking=True
        )
        await hass.async_block_till_done()

        assert hass.states.get(switch).state == "on"
        assert hass.states.get(mode).state == "idle"
        assert entry.options["tariff_enabled"] is True
    finally:
        for p in patches:
            p.stop()

"""Tariff fields in Configure can be cleared (1.9.12; #21).

They used default=, and Home Assistant leaves a cleared field out of the
submission, so voluptuous put the old value straight back: cleared active
hours were saved as 0 and 0.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.sunsynk.config_flow import SunsynkOptionsFlow


def _flow(options):
    entry = MagicMock()
    entry.entry_id = "entry"
    entry.data = {
        "api_server": "api.sunsynk.net",
        "username": "user@example.com",
        "password": "secret",
        "serials": ["INV1"],
    }
    entry.options = options
    flow = SunsynkOptionsFlow(entry)
    flow.hass = SimpleNamespace(
        config=SimpleNamespace(latitude=51.0, longitude=-1.0),
        config_entries=MagicMock(),
    )
    flow.hass.config_entries.async_entries.return_value = []
    return flow


@pytest.mark.asyncio
async def test_tariff_text_fields_can_be_cleared():
    flow = _flow(
        {
            "price_entity": "sensor.price",
            "tariff_start_hour": 0,
            "tariff_end_hour": 0,
            "cheap_threshold": 0.1,
        }
    )
    form = await flow.async_step_init()
    schema = form["data_schema"]
    keys = {str(key): key for key in schema.schema}

    assert keys["tariff_start_hour"].description == {"suggested_value": "0"}
    assert keys["cheap_threshold"].description == {"suggested_value": "0.1"}
    validated = schema({})
    assert "tariff_start_hour" not in validated
    assert "tariff_end_hour" not in validated
    assert "cheap_threshold" not in validated


@pytest.mark.asyncio
async def test_unset_tariff_text_fields_have_no_suggestion():
    form = await _flow({}).async_step_init()
    keys = {str(key): key for key in form["data_schema"].schema}

    assert keys["tariff_start_hour"].description == {"suggested_value": None}

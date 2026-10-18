"""1.9.x: the dashboard can be turned off, and stays on by default (#38)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.sunsynk import _dashboard_enabled
from custom_components.sunsynk.config_flow import SunsynkOptionsFlow


@pytest.mark.parametrize(
    ("data", "options", "enabled"),
    [
        ({}, {}, True),  # existing installs keep their dashboard
        ({}, {"create_dashboard": False}, False),
        ({"create_dashboard": False}, {}, False),
        ({"create_dashboard": False}, {"create_dashboard": True}, True),
    ],
)
def test_dashboard_is_on_unless_turned_off(data, options, enabled):
    assert _dashboard_enabled(SimpleNamespace(data=data, options=options)) is enabled


async def _submit(options, user_input):
    entry = MagicMock(
        entry_id="entry",
        data={"api_server": "api.sunsynk.net", "username": "u", "password": "p",
              "serials": ["INV1"]},
        options=options,
    )
    flow = SunsynkOptionsFlow(entry)
    flow.hass = SimpleNamespace(
        config=SimpleNamespace(latitude=51.0, longitude=-1.0),
        config_entries=MagicMock(),
    )
    flow.hass.config_entries.async_entries.return_value = []
    return await flow.async_step_init(user_input)


@pytest.mark.asyncio
async def test_options_form_offers_the_dashboard_option_ticked_by_default():
    result = await _submit({}, None)

    field = next(m for m in result["data_schema"].schema if str(m) == "create_dashboard")
    assert field.default() is True


@pytest.mark.asyncio
@pytest.mark.parametrize("choice", [True, False])
async def test_options_form_saves_the_dashboard_choice(choice):
    result = await _submit(
        {},
        {"api_server": "api.sunsynk.net", "username": "u", "password": "",
         "serials": "INV1", "refresh_interval": 300, "create_dashboard": choice},
    )

    assert result["type"] == "create_entry"
    assert result["data"]["create_dashboard"] is choice

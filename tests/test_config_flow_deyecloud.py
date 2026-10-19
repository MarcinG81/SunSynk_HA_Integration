"""Adding Deye Cloud inverters, and their (read-only) options."""

from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.sunsynk.config_flow import (
    SunsynkConfigFlow,
    SunsynkOptionsFlow,
    _async_list_deyecloud_inverters,
)
from custom_components.sunsynk.deyecloud.client import (
    DeyeCloudApiError,
    DeyeCloudAuthError,
)

_LIST = "custom_components.sunsynk.config_flow._async_list_deyecloud_inverters"

INVERTERS = [
    {"deviceSn": "2301", "stationName": "Home"},
    {"deviceSn": "2302", "stationName": None},
]


def _input(**updates):
    data = {
        "region": "eu",
        "app_id": " 202310190001 ",
        "app_secret": "secret ",
        "login": "Me@Example.com",
        "password": "hunter2",
        "refresh_interval": 300,
    }
    data.update(updates)
    return data


def _flow():
    flow = SunsynkConfigFlow()
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = MagicMock()
    return flow


@pytest.mark.asyncio
async def test_the_form_asks_for_the_app_and_login():
    result = await _flow().async_step_deyecloud()

    assert result["type"] == "form"
    assert result["step_id"] == "deyecloud"
    fields = {str(key) for key in result["data_schema"].schema}
    assert fields == {
        "region", "app_id", "app_secret", "login", "password", "company_id",
        "refresh_interval",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raised", "error"),
    [
        (DeyeCloudAuthError("auth appId not found"), "invalid_auth_deyecloud"),
        (DeyeCloudApiError("HTTP 500"), "cannot_connect_deyecloud"),
        (RuntimeError("boom"), "cannot_connect_deyecloud"),
    ],
)
async def test_login_problems_are_shown_on_the_form(raised, error):
    with patch(_LIST, AsyncMock(side_effect=raised)):
        result = await _flow().async_step_deyecloud(_input())

    assert result["errors"] == {"base": error}
    # What was typed stays, except the password.
    suggested = {
        str(key): key.description.get("suggested_value")
        for key in result["data_schema"].schema
        if key.description
    }
    assert suggested["login"] == "Me@Example.com"
    assert "password" not in suggested


@pytest.mark.asyncio
async def test_an_account_without_inverters_is_refused():
    with patch(_LIST, AsyncMock(return_value=[])):
        result = await _flow().async_step_deyecloud(_input())

    assert result["errors"] == {"base": "no_inverters"}


@pytest.mark.asyncio
async def test_inverters_are_picked_and_only_the_password_hash_is_kept():
    flow = _flow()
    with patch(_LIST, AsyncMock(return_value=INVERTERS)) as listed:
        pick = await flow.async_step_deyecloud(_input(company_id=" 42 "))

    sent = listed.await_args.args[0]
    assert sent["app_id"] == "202310190001"
    assert sent["app_secret"] == "secret"
    assert sent["company_id"] == "42"
    flow.async_set_unique_id.assert_awaited_once_with("deyecloud_eu_me@example.com")
    assert pick["step_id"] == "deyecloud_inverters"
    choices = pick["data_schema"].schema
    (field,) = choices
    assert choices[field].options == {"2301": "2301 — Home", "2302": "2302"}

    result = await flow.async_step_deyecloud_inverters({"serials": ["2302"]})

    assert result["type"] == "create_entry"
    assert result["title"] == "Deye Cloud (2302)"
    data = result["data"]
    assert data["backend"] == "deyecloud"
    assert data["serials"] == ["2302"]
    assert data["password_hash"] == hashlib.sha256(b"hunter2").hexdigest()
    assert "password" not in data


@pytest.mark.asyncio
async def test_picking_no_inverter_is_refused():
    flow = _flow()
    with patch(_LIST, AsyncMock(return_value=INVERTERS)):
        await flow.async_step_deyecloud(_input())

    result = await flow.async_step_deyecloud_inverters({"serials": []})

    assert result["errors"] == {"serials": "invalid_serials"}


@pytest.mark.asyncio
async def test_listing_logs_in_with_what_was_entered():
    client = MagicMock()
    client.async_list_inverters = AsyncMock(return_value=INVERTERS)
    with patch(
        "custom_components.sunsynk.config_flow.DeyeCloudClient", return_value=client
    ) as made:
        inverters = await _async_list_deyecloud_inverters(
            {
                "region": "us",
                "app_id": "app",
                "app_secret": "secret",
                "login": "me",
                "password_hash": "hash",
            }
        )

    assert inverters == INVERTERS
    args = made.call_args
    assert args.args[1:] == ("us", "app", "secret", "me", "hash")
    assert args.kwargs == {"company_id": None}


def _options(options=None):
    entry = MagicMock()
    entry.data = {"backend": "deyecloud", "refresh_interval": 600}
    entry.options = options or {}
    return SunsynkOptionsFlow(entry)


@pytest.mark.asyncio
async def test_options_offer_only_the_refresh_interval():
    result = await _options().async_step_init()

    assert result["step_id"] == "deyecloud"
    (field,) = result["data_schema"].schema
    assert str(field) == "refresh_interval"
    assert field.default() == 600


@pytest.mark.asyncio
async def test_options_save_the_refresh_interval():
    result = await _options({"other": 1}).async_step_init({"refresh_interval": 900})

    assert result["type"] == "create_entry"
    assert result["data"] == {"other": 1, "refresh_interval": 900}

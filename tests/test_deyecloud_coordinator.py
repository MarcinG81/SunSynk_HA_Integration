"""The Deye Cloud coordinator: reads like the Sunsynk one, refuses writes."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from custom_components.sunsynk.deyecloud import coordinator as module
from custom_components.sunsynk.deyecloud.client import (
    DeyeCloudApiError,
    DeyeCloudAuthError,
    DeyeCloudUnsupportedError,
)
from custom_components.sunsynk.deyecloud.coordinator import DeyeCloudCoordinator

_DATA = json.loads(
    (Path(__file__).parent / "fixtures" / "deyecloud_37.json").read_text("utf-8")
)
SINGLE = _DATA["single_phase_8kw"]["latest"]
THREE = _DATA["three_phase_15kw"]["latest"]
THREE_CONFIG = _DATA["three_phase_15kw"]["config"]


def _base_init(self, hass, *args, **kwargs):
    # DataUpdateCoordinator needs Home Assistant's frame helper, which this
    # suite's lightweight hass doesn't set up.
    self.hass = hass
    self.data = None


@pytest.fixture
def issues():
    with (
        patch.object(module.ir, "async_create_issue") as create,
        patch.object(module.ir, "async_delete_issue") as delete,
    ):
        yield create, delete


@pytest.fixture
def client():
    client = MagicMock()
    client.async_get_latest = AsyncMock(return_value={"SN1": SINGLE, "SN3": THREE})

    async def get_config(serial, kind):
        if serial == "SN1":
            raise DeyeCloudUnsupportedError("config point not supported")
        return THREE_CONFIG[kind]

    client.async_get_config = AsyncMock(side_effect=get_config)
    return client


@pytest.fixture
def coordinator(client):
    hass = MagicMock()
    hass.loop.time.return_value = 1000.0
    with patch.object(DataUpdateCoordinator, "__init__", _base_init):
        coordinator = DeyeCloudCoordinator(
            hass,
            region="eu",
            app_id="app",
            app_secret="secret",
            login="me",
            password_hash="hash",
            serials=["SN1", "SN3"],
            refresh_interval=300,
            entry_id="entry",
        )
    coordinator._async_client = AsyncMock(return_value=client)
    return coordinator


@pytest.mark.asyncio
async def test_reads_every_inverter_in_the_sunsynk_shape(coordinator, issues):
    data = await coordinator._async_update_data()

    assert data["SN1"]["battery"]["soc"] == 100
    assert data["SN3"]["battery"]["soc"] == 31
    assert data["SN3"]["settings"]["sysWorkMode"] == 1
    assert data["SN1"]["settings"] == {}
    assert data["SN1"]["plant"] == {}
    _, delete = issues
    delete.assert_any_call(coordinator.hass, "sunsynk", "auth_failed_entry")


@pytest.mark.asyncio
async def test_settings_deye_cloud_doesnt_offer_are_not_asked_for_again(
    coordinator, client, issues
):
    await coordinator._async_update_data()
    assert coordinator.can_configure("SN1") is False
    assert coordinator.can_configure("SN3") is True

    client.async_get_config.reset_mock()
    coordinator.hass.loop.time.return_value = 1000.0 + module._CONFIG_MAX_AGE
    await coordinator._async_update_data()

    asked = {call.args[0] for call in client.async_get_config.call_args_list}
    assert asked == {"SN3"}


@pytest.mark.asyncio
async def test_settings_are_read_at_most_every_half_hour(coordinator, client, issues):
    await coordinator._async_update_data()
    client.async_get_config.reset_mock()

    coordinator.hass.loop.time.return_value = 1000.0 + module._CONFIG_MAX_AGE - 1
    data = await coordinator._async_update_data()

    client.async_get_config.assert_not_called()
    assert data["SN3"]["settings"]["batteryMaxCurrentDischarge"] == 170


@pytest.mark.asyncio
async def test_a_failed_settings_read_keeps_the_last_one(coordinator, client, issues):
    await coordinator._async_update_data()
    client.async_get_config.side_effect = DeyeCloudApiError("timed out")
    coordinator.hass.loop.time.return_value = 1000.0 + module._CONFIG_MAX_AGE

    data = await coordinator._async_update_data()

    assert data["SN3"]["settings"]["sysWorkMode"] == 1


@pytest.mark.asyncio
async def test_an_inverter_without_data_keeps_the_rest_but_no_soc(
    coordinator, client, issues
):
    coordinator.data = {"SN1": {"battery": {"soc": 80}, "pv": {"pac": 5}}}
    client.async_get_latest.return_value = {"SN3": THREE}

    data = await coordinator._async_update_data()

    assert data["SN1"] == {"battery": {}, "pv": {"pac": 5}}
    create, _ = issues
    assert create.call_args.args[2] == "inverter_offline_SN1"


@pytest.mark.asyncio
async def test_no_data_for_any_inverter_fails_the_update(coordinator, client, issues):
    client.async_get_latest.return_value = {}

    with pytest.raises(UpdateFailed, match="no data for any inverter"):
        await coordinator._async_update_data()


@pytest.mark.asyncio
@pytest.mark.parametrize("where", ["latest", "config"])
async def test_a_refused_login_raises_a_repair(coordinator, client, issues, where):
    if where == "latest":
        client.async_get_latest.side_effect = DeyeCloudAuthError("bad secret")
    else:
        client.async_get_config.side_effect = DeyeCloudAuthError("bad secret")

    with pytest.raises(UpdateFailed, match="login failed"):
        await coordinator._async_update_data()

    create, _ = issues
    assert create.call_args.args[2] == "auth_failed_entry"


@pytest.mark.asyncio
async def test_an_api_error_fails_the_update(coordinator, client, issues):
    client.async_get_latest.side_effect = DeyeCloudApiError("HTTP 500")

    with pytest.raises(UpdateFailed, match="HTTP 500"):
        await coordinator._async_update_data()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("async_write_setting", ("SN3", "sysWorkMode", 0)),
        ("async_write_settings", ("SN3", {"sysWorkMode": 0})),
        ("async_write_plant_price", ("SN3", 0.2)),
    ],
)
async def test_writes_are_refused_for_now(coordinator, method, args):
    with pytest.raises(HomeAssistantError, match="isn't supported yet"):
        await getattr(coordinator, method)(*args)


@pytest.mark.asyncio
async def test_the_client_is_made_once_per_session():
    hass = MagicMock()
    with patch.object(DataUpdateCoordinator, "__init__", _base_init):
        coordinator = DeyeCloudCoordinator(
            hass,
            region="eu",
            app_id="app",
            app_secret="secret",
            login="me",
            password_hash="hash",
            serials=["SN1"],
            refresh_interval=300,
            company_id="42",
        )
    session = MagicMock(closed=False)
    coordinator._session = session

    first = await coordinator._async_client()
    second = await coordinator._async_client()

    assert first is second
    assert first.session is session
    assert first._company_id == "42"
    assert first._base.startswith("https://eu1-developer")



@pytest.mark.asyncio
@pytest.mark.parametrize(
    "meta",
    [
        {"_deviceState": 3},  # Deye says offline
        {"_collectionTime": 1_000_000_000},  # 2001: hours old
    ],
    ids=["offline", "old"],
)
async def test_an_offline_logger_s_last_values_are_not_taken_as_now(
    coordinator, client, issues, meta
):
    """Deye Cloud keeps answering with an offline logger's last points: two
    inverters in #37 showed the same values for hours. No SOC from them."""
    client.async_get_latest.return_value = {"SN1": {**SINGLE, **meta}, "SN3": THREE}

    data = await coordinator._async_update_data()

    assert data["SN1"]["battery"] == {}
    assert data["SN1"]["pv"]["etoday"] == 1.4  # still shown as last reported
    assert data["SN3"]["battery"]["soc"] == 31
    create, _ = issues
    assert create.call_args.args[2] == "inverter_offline_SN1"


@pytest.mark.asyncio
async def test_recent_points_are_used(coordinator, client, issues):
    now = dt_util.utcnow().timestamp()
    client.async_get_latest.return_value = {
        "SN1": {**SINGLE, "_collectionTime": now - 300, "_deviceState": 1},
        "SN3": THREE,
    }

    data = await coordinator._async_update_data()

    assert data["SN1"]["battery"]["soc"] == 100
    create, _ = issues
    create.assert_not_called()

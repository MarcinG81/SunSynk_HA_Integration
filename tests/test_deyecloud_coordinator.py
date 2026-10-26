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
    with pytest.raises(HomeAssistantError, match="experimental controls"):
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



# ── Experimental controls ──────────────────────────────────────────────────


@pytest.fixture
def controlled(coordinator, client):
    coordinator._controls = True
    coordinator.data = {"SN3": {"settings": {"sysWorkMode": 1}}}
    coordinator.async_update_listeners = MagicMock()
    for name in (
        "async_set_work_mode",
        "async_set_energy_pattern",
        "async_set_battery_current",
        "async_set_max_sell_power",
    ):
        setattr(client, name, AsyncMock(return_value={"status": 666}))
    coordinator._config_read_at["SN3"] = 1000.0
    return coordinator


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("key", "value", "method", "args"),
    [
        ("sysWorkMode", 0, "async_set_work_mode", ("SN3", 0)),
        ("energyMode", 1, "async_set_energy_pattern", ("SN3", 1)),
        ("batteryMaxCurrentCharge", 80, "async_set_battery_current", ("SN3", "charge", 80)),
        ("batteryMaxCurrentDischarge", 90, "async_set_battery_current", ("SN3", "discharge", 90)),
        ("solarMaxSellPower", 6000, "async_set_max_sell_power", ("SN3", 6000)),
    ],
)
async def test_each_setting_goes_out_as_its_order(
    controlled, client, key, value, method, args
):
    await controlled.async_write_setting("SN3", key, value)

    getattr(client, method).assert_awaited_once_with(*args)
    assert controlled.data["SN3"]["settings"][key] == value
    controlled.async_update_listeners.assert_called_once()
    assert "SN3" not in controlled._config_read_at  # read again next refresh


@pytest.mark.asyncio
async def test_the_timer_table_isn_t_changed(controlled):
    """Its read has no Sell flags: a write would untick Sell on every slot."""
    with pytest.raises(HomeAssistantError, match="can't change cap1"):
        await controlled.async_write_settings("SN3", {"cap1": 50})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raised", "match"),
    [
        (module.DeyeCloudBusyError("busy"), "busy with another change"),
        (DeyeCloudApiError("order failed"), "couldn't change sysWorkMode"),
        (DeyeCloudAuthError("token"), "login failed"),
    ],
)
async def test_a_refused_order_is_explained(controlled, client, issues, raised, match):
    client.async_set_work_mode.side_effect = raised

    with pytest.raises(HomeAssistantError, match=match):
        await controlled.async_write_setting("SN3", "sysWorkMode", 0)

    assert controlled.data["SN3"]["settings"]["sysWorkMode"] == 1


@pytest.mark.asyncio
async def test_a_value_out_of_range_is_refused_before_any_order(controlled, client):
    from custom_components.sunsynk.write_validation import (
        SunsynkSettingValidationError,
    )

    with pytest.raises(SunsynkSettingValidationError):
        await controlled.async_write_setting("SN3", "sysWorkMode", 7)

    client.async_set_work_mode.assert_not_called()


@pytest.mark.asyncio
async def test_without_data_yet_the_order_still_goes(controlled, client):
    controlled.data = None

    await controlled.async_write_setting("SN3", "solarMaxSellPower", 5000)

    client.async_set_max_sell_power.assert_awaited_once()



@pytest.mark.asyncio
async def test_going_offline_and_back_is_logged_once_each(
    coordinator, client, issues, caplog
):
    client.async_get_latest.return_value = {
        "SN1": {**SINGLE, "_deviceState": 3},
        "SN3": {**THREE, "_collectionTime": 1_000_000_000},
    }
    for _ in range(3):
        await coordinator._async_update_data()

    assert caplog.text.count("Deye Cloud reports inverter SN1 offline") == 1
    assert caplog.text.count("Inverter SN3 hasn't reported to Deye Cloud since") == 1

    client.async_get_latest.return_value = {"SN1": SINGLE, "SN3": THREE}
    await coordinator._async_update_data()
    await coordinator._async_update_data()

    assert caplog.text.count("Inverter SN1 is reporting to Deye Cloud again") == 1



@pytest.mark.asyncio
async def test_settings_deye_cloud_doesn_t_offer_are_logged_once_per_inverter(
    coordinator, issues, caplog
):
    caplog.set_level("INFO")

    await coordinator._async_update_data()

    lines = [r.getMessage() for r in caplog.records if "doesn't offer" in r.getMessage()]
    assert lines == [
        "Deye Cloud doesn't offer the system, battery, tou settings of inverter SN1; "
        "it is read-only"
    ]

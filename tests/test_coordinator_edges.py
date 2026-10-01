"""Coverage for coordinator error and lifecycle boundaries."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.sunsynk.api.auth import SunsynkAuthError
from custom_components.sunsynk.api.client import (
    SunsynkApiError,
    SunsynkAuthenticationError,
)
from custom_components.sunsynk.coordinator import SunsynkCoordinator


def _bare(**updates):
    auth = MagicMock(_api_server="api.example")
    auth.async_get_token = AsyncMock(return_value="token")
    obj = SimpleNamespace(
        hass=MagicMock(),
        _auth=auth,
        _entry_id="entry",
        serials=["SN1"],
        data={"SN1": {"settings": {}, "plant": {}}},
        _async_get_session=AsyncMock(return_value=MagicMock()),
        async_request_refresh=AsyncMock(),
    )
    obj.__dict__.update(updates)
    return obj


@pytest.mark.asyncio
async def test_real_coordinator_initializes_session_and_closes(mock_hass):
    with patch("homeassistant.helpers.frame.report_usage"):
        coordinator = SunsynkCoordinator(
            mock_hass, MagicMock(), ["SN1"], 60, "entry"
        )
    session = MagicMock(closed=False, close=AsyncMock())
    with patch(
        "custom_components.sunsynk.coordinator.aiohttp.ClientSession",
        return_value=session,
    ):
        assert await coordinator._async_get_session() is session
        assert await coordinator._async_get_session() is session
    await coordinator.async_close()
    session.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_auth_failure_is_reported():
    obj = _bare()
    obj._auth.async_get_token.side_effect = SunsynkAuthError("bad")
    with patch("custom_components.sunsynk.coordinator.ir") as issue_registry:
        with pytest.raises(UpdateFailed, match="Authentication failed"):
            await SunsynkCoordinator._async_update_data(obj)
    issue_registry.async_create_issue.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("plant_error", "message"),
    [
        (SunsynkAuthenticationError("401"), "Authentication failed"),
        (SunsynkApiError("offline"), None),
    ],
)
async def test_update_plant_failures(plant_error, message):
    obj = _bare(data={"SN1": {"plant": {"cached": True}}})
    client = MagicMock(
        async_fetch_all=AsyncMock(
            return_value={"inverter": {"plant": {"id": 7}}}
        ),
        async_get_plant_info=AsyncMock(side_effect=plant_error),
    )
    with (
        patch("custom_components.sunsynk.coordinator.SunsynkClient", return_value=client),
        patch("custom_components.sunsynk.coordinator.ir"),
    ):
        if message:
            with pytest.raises(UpdateFailed, match=message):
                await SunsynkCoordinator._async_update_data(obj)
            obj._auth.invalidate_token.assert_called_once()
        else:
            result = await SunsynkCoordinator._async_update_data(obj)
            assert result["SN1"]["plant"] == {"cached": True}


def test_unknown_setting_uses_own_group():
    assert SunsynkCoordinator._allowed_setting_group("futureKey") == frozenset(
        {"futureKey"}
    )


@pytest.mark.asyncio
async def test_empty_setting_batch_is_noop():
    obj = _bare()
    await SunsynkCoordinator.async_write_settings(obj, "SN1", {})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("token_error", "settings_error", "expected"),
    [
        (SunsynkAuthError("bad"), None, "Authentication failed"),
        (None, SunsynkAuthenticationError("401"), "Authentication failed"),
        (None, SunsynkApiError("offline"), "Cannot read settings"),
    ],
)
async def test_execute_batch_read_failures(token_error, settings_error, expected):
    obj = _bare()
    if token_error:
        obj._auth.async_get_token.side_effect = token_error
    client = MagicMock(async_get_settings=AsyncMock(side_effect=settings_error))
    with patch("custom_components.sunsynk.coordinator.SunsynkClient", return_value=client):
        failures = await SunsynkCoordinator._async_execute_setting_batch(
            obj, "SN1", {"time1on": 1}
        )
    assert expected in str(failures["time1on"])


@pytest.mark.asyncio
async def test_drain_skips_cancelled_waiter_and_recovers_internal_error():
    obj = _bare()
    SunsynkCoordinator._ensure_write_state(obj)
    loop = asyncio.get_running_loop()
    done = loop.create_future()
    done.cancel()
    obj._pending_setting_writes["SN1"] = {"time1on": 1}
    obj._pending_setting_waiters["SN1"] = [(done, frozenset({"time1on"}))]
    obj._write_drain_scheduled.add("SN1")
    with patch.object(
        SunsynkCoordinator,
        "_async_execute_setting_batch",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        await SunsynkCoordinator._async_drain_write_queue(obj, "SN1")
    assert done.cancelled()


@pytest.mark.asyncio
async def test_drain_skips_already_done_waiter():
    obj = _bare()
    SunsynkCoordinator._ensure_write_state(obj)
    done = asyncio.get_running_loop().create_future()
    done.set_result(None)
    obj._pending_setting_writes["SN1"] = {"time1on": 1}
    obj._pending_setting_waiters["SN1"] = [(done, frozenset({"time1on"}))]
    with patch.object(
        SunsynkCoordinator,
        "_async_execute_setting_batch",
        new=AsyncMock(return_value={}),
    ):
        await SunsynkCoordinator._async_drain_write_queue(obj, "SN1")


@pytest.mark.asyncio
async def test_drain_propagates_internal_error_to_late_waiter():
    obj = _bare()
    SunsynkCoordinator._ensure_write_state(obj)
    first = asyncio.get_running_loop().create_future()
    late = asyncio.get_running_loop().create_future()
    obj._pending_setting_writes["SN1"] = {"time1on": 1}
    obj._pending_setting_waiters["SN1"] = [(first, frozenset({"time1on"}))]

    async def _fail(*_args):
        obj._pending_setting_waiters["SN1"] = [(late, frozenset({"cap1"}))]
        raise RuntimeError("boom")

    with patch.object(
        SunsynkCoordinator,
        "_async_execute_setting_batch",
        new=AsyncMock(side_effect=_fail),
    ):
        await SunsynkCoordinator._async_drain_write_queue(obj, "SN1")
    assert isinstance(first.exception(), RuntimeError)
    assert isinstance(late.exception(), RuntimeError)


@pytest.mark.asyncio
async def test_drain_reschedules_work_arriving_at_cleanup():
    class RaceDict(dict):
        def pop(self, key, default=None):
            return None

        def get(self, key, default=None):
            return {"time1on": 1}

    obj = _bare()
    SunsynkCoordinator._ensure_write_state(obj)
    obj._pending_setting_writes = RaceDict()
    with patch.object(SunsynkCoordinator, "_launch_write_drain", MagicMock()) as launch:
        await SunsynkCoordinator._async_drain_write_queue(obj, "SN1")
        await asyncio.sleep(0)
    launch.assert_called_once_with(obj, "SN1")


@pytest.mark.asyncio
async def test_single_write_verification_wrapper():
    obj = _bare()
    client = MagicMock()
    with patch.object(
        SunsynkCoordinator, "_async_verify_writes", new=AsyncMock()
    ) as verify:
        await SunsynkCoordinator._async_verify_write(
            obj, client, MagicMock(), "SN1", "time1on", 1
        )
    verify.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_waits_for_active_write_tasks():
    obj = _bare()
    SunsynkCoordinator._ensure_write_state(obj)
    task = asyncio.create_task(asyncio.sleep(0))
    obj._write_drain_tasks["SN1"] = task
    obj._session = None
    await SunsynkCoordinator.async_close(obj)
    assert task.done()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stage", "error", "match"),
    [
        ("token", SunsynkAuthError("bad"), "Authentication failed"),
        ("inverter", SunsynkAuthenticationError("401"), "Authentication failed"),
        ("inverter", SunsynkApiError("offline"), "Cannot read inverter info"),
        ("no_plant", None, "No plant found"),
        ("plant", SunsynkAuthenticationError("401"), "Authentication failed"),
        ("plant", SunsynkApiError("offline"), "Cannot read plant info"),
        ("write", SunsynkAuthenticationError("401"), "Authentication failed"),
        ("write", SunsynkApiError("offline"), "Failed to write plant price"),
    ],
)
async def test_plant_price_api_failures(stage, error, match):
    obj = _bare()
    if stage == "token":
        obj._auth.async_get_token.side_effect = error
    valid_plant = {
        "id": 7,
        "currency": {"id": 1},
        "invest": 0,
        "charges": [{"type": 1, "price": 0.2}],
    }
    client = MagicMock(
        async_get_inverter_info=AsyncMock(return_value={"plant": {"id": 7}}),
        async_get_plant_info=AsyncMock(return_value=valid_plant),
        async_set_plant_income=AsyncMock(),
    )
    if stage == "inverter":
        client.async_get_inverter_info.side_effect = error
    elif stage == "no_plant":
        client.async_get_inverter_info.return_value = {}
    elif stage == "plant":
        obj.data["SN1"]["plant"] = {"id": 7}
        client.async_get_plant_info.side_effect = error
    elif stage == "write":
        obj.data["SN1"]["plant"] = {"id": 7}
        client.async_set_plant_income.side_effect = error

    with patch("custom_components.sunsynk.coordinator.SunsynkClient", return_value=client):
        with pytest.raises(UpdateFailed, match=match):
            await SunsynkCoordinator._async_write_plant_price_locked(obj, "SN1", 0.3)

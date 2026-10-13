"""Writes built from real inverter settings (1.9.11; #21, #26, #31, #32, #33).

Fixtures are the System Mode fields of two real diagnostics dumps, with
identifiers replaced: a single Deye-built 5 kW (strings throughout,
sellTime{n}En) and a Sunsynk parallel master (real booleans, sellTime{n}on).
Predbat confirmed live that the API silently drops time{n}on when the write
lacks the slot's sell flag or sends a flag in a different form from its
read-back, while every other field of the same write persists.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.sunsynk.api.client import SunsynkApiError
from custom_components.sunsynk.coordinator import SunsynkCoordinator
from custom_components.sunsynk.switch import WRITABLE_SWITCHES, SunsynkSwitchEntity

_FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures" / "system_mode_settings.json").read_text()
)


def _coordinator(settings: dict, serials=None) -> SimpleNamespace:
    auth = MagicMock(_api_server="api.sunsynk.net")
    auth.async_get_token = AsyncMock(return_value="token")
    serial = settings["sn"]
    return SimpleNamespace(
        hass=MagicMock(),
        _auth=auth,
        serials=serials or [serial],
        _async_get_session=AsyncMock(return_value=MagicMock()),
        async_request_refresh=AsyncMock(),
        data={serial: {"settings": dict(settings), "inverter": {}}},
    )


async def _write(settings: dict, key: str, value, fresh=None) -> dict:
    coordinator = _coordinator(settings)
    sent: list[dict] = []
    client = MagicMock()
    client.async_write_settings = AsyncMock(
        side_effect=lambda session, serial, payload: sent.append(dict(payload))
    )
    reads = [fresh if fresh is not None else dict(settings)]

    async def _read(session, serial):
        return reads.pop(0) if reads else dict(sent[-1])

    client.async_get_settings = AsyncMock(side_effect=_read)
    with (
        patch("custom_components.sunsynk.coordinator.SunsynkClient", return_value=client),
        patch("custom_components.sunsynk.coordinator.asyncio.sleep", AsyncMock()),
        patch("custom_components.sunsynk.coordinator.ir"),
    ):
        await SunsynkCoordinator.async_write_setting(
            coordinator, settings["sn"], key, value
        )
        await asyncio.gather(*list(coordinator._verify_tasks))
    return sent[0]


@pytest.mark.asyncio
async def test_deye_grid_charge_write_matches_what_the_api_accepts():
    deye = _FIXTURES["deye_5k"]

    payload = await _write(deye, "time5on", 1)

    assert payload.keys() == deye.keys()
    assert all(payload[f"sellTime{n}En"] == deye[f"sellTime{n}En"] for n in range(1, 7))
    assert payload["time5on"] == "true"
    assert {k: v for k, v in payload.items() if k != "time5on"} == {
        k: v for k, v in deye.items() if k != "time5on"
    }


@pytest.mark.asyncio
async def test_deye_number_write_is_sent_as_text():
    payload = await _write(_FIXTURES["deye_5k"], "cap5", 40.0)

    assert payload["cap5"] == "40"


@pytest.mark.asyncio
async def test_sunsynk_model_keeps_real_booleans_and_its_own_sell_flag():
    sunsynk = _FIXTURES["sunsynk_parallel_master"]

    payload = await _write(sunsynk, "time1on", 1)

    assert payload["time1on"] is True
    assert "sellTime1on" in payload
    assert not any(k.startswith("sellTime") and k.endswith("En") for k in payload)


@pytest.mark.asyncio
async def test_change_made_in_the_app_since_the_last_poll_is_not_undone():
    deye = _FIXTURES["deye_5k"]
    in_the_app_now = {**deye, "sellTime3": "08:30"}

    payload = await _write(deye, "time5on", 1, fresh=in_the_app_now)

    assert payload["sellTime3"] == "08:30"


@pytest.mark.asyncio
async def test_failed_fresh_read_falls_back_to_the_cache():
    deye = _FIXTURES["deye_5k"]
    coordinator = _coordinator(deye)
    sent: list[dict] = []
    client = MagicMock()
    client.async_write_settings = AsyncMock(
        side_effect=lambda session, serial, payload: sent.append(dict(payload))
    )
    client.async_get_settings = AsyncMock(side_effect=SunsynkApiError("boom"))
    with (
        patch("custom_components.sunsynk.coordinator.SunsynkClient", return_value=client),
        patch("custom_components.sunsynk.coordinator.asyncio.sleep", AsyncMock()),
        patch("custom_components.sunsynk.coordinator.ir"),
    ):
        await SunsynkCoordinator.async_write_settings(
            coordinator, "DEYE1", {"time5on": 1, "cap5": 30}
        )
        await asyncio.gather(*list(coordinator._verify_tasks))

    # Both changes go out in a single request.
    assert len(sent) == 1
    assert sent[0]["time5on"] == "true"
    assert sent[0]["cap5"] == "30"


def test_sell_time_switch_uses_the_deye_sell_flag():
    deye = _FIXTURES["deye_5k"]
    coordinator = MagicMock()
    coordinator.data = {"DEYE1": {"settings": dict(deye)}}
    coordinator.async_write_setting = AsyncMock()
    description = next(d for d in WRITABLE_SWITCHES if d.setting_key == "sellTime3on")
    switch = SunsynkSwitchEntity(coordinator, "DEYE1", description, MagicMock())

    assert switch.is_on is False
    asyncio.run(switch.async_turn_on())

    coordinator.async_write_setting.assert_awaited_once_with("DEYE1", "sellTime3En", 1)


def test_parallel_group_is_recognised_from_settings():
    coordinator = SimpleNamespace(
        serials=["2406060175", "2406060181"],
        data={
            "2406060175": {"inverter": {}, "settings": {"parallel": 1, "equipMode": 0}},
            "2406060181": {"inverter": {}, "settings": {"parallel": "1", "equipMode": "1"}},
        },
    )

    assert SunsynkCoordinator.write_target_serials.fget(coordinator) == ["2406060181"]
    assert (
        SunsynkCoordinator._resolve_parallel_write_target(coordinator, "2406060175", "x")
        == "2406060181"
    )


def test_independent_inverters_are_not_merged():
    coordinator = SimpleNamespace(
        serials=["A", "B"],
        data={
            "A": {"settings": {"parallel": 0, "equipMode": 0}},
            "B": {"settings": {"parallel": 0, "equipMode": 1}},
        },
    )

    assert SunsynkCoordinator.write_target_serials.fget(coordinator) == ["A", "B"]

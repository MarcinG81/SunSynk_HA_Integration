"""Writes built from real inverter settings (#26, #32, #33).

Fixtures are the System Mode fields of two real diagnostics dumps, with
identifiers replaced: a single Deye 5 kW (strings throughout, sellTime{n}En)
and a Sunsynk parallel master (real booleans and ints, sellTime{n}on).

Predbat confirmed live that the API silently drops time{n}on when the write
lacks the slot's sell flag or sends the flag in a different form from its
read-back, while every other field of the same write persists.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.sunsynk.coordinator import SunsynkCoordinator
from custom_components.sunsynk.switch import WRITABLE_SWITCHES, SunsynkSwitchEntity

_FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures" / "system_mode_settings.json").read_text()
)


def _coordinator(settings: dict) -> SimpleNamespace:
    auth = MagicMock(_api_server="api.sunsynk.net")
    auth.async_get_token = AsyncMock(return_value="token")
    serial = settings["sn"]
    return SimpleNamespace(
        hass=MagicMock(),
        _auth=auth,
        serials=[serial],
        _async_get_session=AsyncMock(return_value=MagicMock()),
        async_request_refresh=AsyncMock(),
        async_update_listeners=MagicMock(),
        data={serial: {"settings": dict(settings), "inverter": {}}},
    )


async def _write(settings: dict, key: str, value) -> dict:
    coordinator = _coordinator(settings)
    sent: list[dict] = []
    client = MagicMock()
    client.async_write_settings = AsyncMock(
        side_effect=lambda session, serial, payload: sent.append(dict(payload))
    )
    client.async_get_settings = AsyncMock(
        side_effect=lambda session, serial: dict(sent[-1] if sent else settings)
    )
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

    # Every System Mode field the inverter reported, nothing else.
    assert payload.keys() == deye.keys()
    # The sell flag of every slot is present (its absence drops time{n}on).
    assert all(payload[f"sellTime{n}En"] == deye[f"sellTime{n}En"] for n in range(1, 7))
    # The changed flag uses the inverter's own "true"/"false" strings.
    assert payload["time5on"] == "true"
    # Everything else exactly as reported.
    assert {k: v for k, v in payload.items() if k != "time5on"} == {
        k: v for k, v in deye.items() if k != "time5on"
    }


@pytest.mark.asyncio
async def test_deye_number_write_is_sent_as_a_string():
    payload = await _write(_FIXTURES["deye_5k"], "cap5", 40)

    assert payload["cap5"] == "40"


@pytest.mark.asyncio
async def test_sunsynk_model_keeps_real_booleans_and_its_own_sell_flag():
    sunsynk = _FIXTURES["sunsynk_parallel_master"]

    payload = await _write(sunsynk, "time1on", 1)

    assert payload["time1on"] is True
    assert payload["mondayOn"] is sunsynk["mondayOn"]
    assert "sellTime1on" in payload
    assert not any(key.startswith("sellTime") and key.endswith("En") for key in payload)


@pytest.mark.asyncio
async def test_sell_time_switch_uses_deye_sell_flag():
    """#33: Deye's per-slot sell tick is sellTime{n}En, not sellTime{n}on."""
    deye = _FIXTURES["deye_5k"]
    coordinator = MagicMock()
    coordinator.data = {"DEYE1": {"settings": dict(deye)}}
    coordinator.async_write_setting = AsyncMock()
    description = next(d for d in WRITABLE_SWITCHES if d.setting_key == "sellTime3on")
    switch = SunsynkSwitchEntity(coordinator, "DEYE1", description, MagicMock())

    assert switch.available is True
    assert switch.is_on is False
    await switch.async_turn_on()

    coordinator.async_write_setting.assert_awaited_once_with("DEYE1", "sellTime3En", 1)

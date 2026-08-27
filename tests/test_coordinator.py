"""Tests for SunsynkCoordinator.async_write_setting cache-staleness handling.

Regression coverage for a real bug: async_request_refresh() is debounced
by Home Assistant (10s cooldown, first call immediate) — so a second
write_setting() call landing within that window would otherwise build its
"preserve the other fields" payload from data that predates the first
write, resending a stale value for whatever the first write just changed
and silently reverting it. This is exactly what
VirtualSlotScheduler._write_window_if_changed does: up to 4 sequential
writes (on/cap/pac/start) to the same settings group, always within
milliseconds of each other.

Instantiating a real SunsynkCoordinator requires the full
pytest-homeassistant-custom-component hass fixture (DataUpdateCoordinator
needs frame-helper setup this repo's lightweight mock_hass doesn't
provide) — nothing else in this suite does that. Instead we call
async_write_setting as an unbound method against a bare object carrying
just the attributes it actually touches, matching the mocking style
already used throughout this suite.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.sunsynk.coordinator import SunsynkCoordinator


@pytest.fixture
def fake_coordinator():
    auth = MagicMock()
    auth._api_server = "api.sunsynk.net"
    auth.async_get_token = AsyncMock(return_value="token")

    return SimpleNamespace(
        _auth=auth,
        _async_get_session=AsyncMock(return_value=MagicMock()),
        async_request_refresh=AsyncMock(),
        data={
            "TEST123": {
                "settings": {
                    "sn": "TEST123",
                    "time1on": "false",
                    "cap1": "50",
                    "sellTime1Pac": "0",
                    "sellTime1": "00:00",
                }
            }
        },
    )


@pytest.mark.asyncio
async def test_second_write_in_a_burst_sees_the_first_writes_change(fake_coordinator):
    """The exact scenario that used to revert VirtualSlotScheduler writes."""
    sent_payloads: list[dict] = []

    mock_client = MagicMock()

    async def _capture_write(session, serial, payload):
        sent_payloads.append(dict(payload))

    mock_client.async_write_settings = AsyncMock(side_effect=_capture_write)

    with patch(
        "custom_components.sunsynk.coordinator.SunsynkClient", return_value=mock_client
    ):
        await SunsynkCoordinator.async_write_setting(fake_coordinator, "TEST123", "time1on", 1)
        await SunsynkCoordinator.async_write_setting(fake_coordinator, "TEST123", "cap1", 90)

    assert len(sent_payloads) == 2
    # The second write's payload must carry the FIRST write's new value for
    # time1on (1), not the stale pre-write value ("false") that was in the
    # cache when the burst started.
    assert sent_payloads[1]["time1on"] == 1
    assert sent_payloads[1]["cap1"] == 90


@pytest.mark.asyncio
async def test_coordinator_cache_updated_immediately_after_write(fake_coordinator):
    mock_client = MagicMock()
    mock_client.async_write_settings = AsyncMock()

    with patch(
        "custom_components.sunsynk.coordinator.SunsynkClient", return_value=mock_client
    ):
        await SunsynkCoordinator.async_write_setting(fake_coordinator, "TEST123", "time1on", 1)

    assert fake_coordinator.data["TEST123"]["settings"]["time1on"] == 1


@pytest.mark.asyncio
async def test_full_slot_arm_sequence_does_not_revert_the_on_flag(fake_coordinator):
    """Reproduces VirtualSlotScheduler._write_window_if_changed's exact write
    order (on, cap, pac, start) for one physical slot. Before the cache fix,
    `on` — written first — was reverted to its pre-burst value by every
    write that followed it in the same debounce window, since each of
    those carried a stale "preserve" copy of it. The slot would end up
    silently disabled despite the code explicitly turning it on.
    """
    sent_payloads: list[dict] = []
    mock_client = MagicMock()

    async def _capture_write(session, serial, payload):
        sent_payloads.append(dict(payload))

    mock_client.async_write_settings = AsyncMock(side_effect=_capture_write)

    with patch(
        "custom_components.sunsynk.coordinator.SunsynkClient", return_value=mock_client
    ):
        await SunsynkCoordinator.async_write_setting(fake_coordinator, "TEST123", "time1on", 1)
        await SunsynkCoordinator.async_write_setting(fake_coordinator, "TEST123", "cap1", 90)
        await SunsynkCoordinator.async_write_setting(fake_coordinator, "TEST123", "sellTime1Pac", 0)
        await SunsynkCoordinator.async_write_setting(fake_coordinator, "TEST123", "sellTime1", "23:30")

    # Every payload from the second one onward must carry the *current*
    # (turned-on) value, not the stale pre-burst "false".
    for payload in sent_payloads[1:]:
        assert payload["time1on"] == 1, "time1on was reverted mid-burst"
    # And the final on-the-wire state (what the server actually ends up
    # with) is consistent across all four fields.
    assert fake_coordinator.data["TEST123"]["settings"]["time1on"] == 1
    assert fake_coordinator.data["TEST123"]["settings"]["cap1"] == 90
    assert fake_coordinator.data["TEST123"]["settings"]["sellTime1Pac"] == 0
    assert fake_coordinator.data["TEST123"]["settings"]["sellTime1"] == "23:30"

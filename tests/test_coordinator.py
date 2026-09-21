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

from custom_components.sunsynk.api.client import SunsynkApiError
from custom_components.sunsynk.const import DOMAIN
from custom_components.sunsynk.coordinator import SunsynkCoordinator


@pytest.fixture(autouse=True)
def _no_verify_write_delay():
    """async_write_setting's verification step sleeps briefly for real
    (see _VERIFY_WRITE_DELAY_SECONDS) to give slower/relayed APIs time to
    apply a write before reading it back — skip that delay in tests."""
    with patch("custom_components.sunsynk.coordinator.asyncio.sleep", AsyncMock()):
        yield


@pytest.fixture
def fake_coordinator():
    auth = MagicMock()
    auth._api_server = "api.sunsynk.net"
    auth.async_get_token = AsyncMock(return_value="token")

    return SimpleNamespace(
        hass=MagicMock(),
        _auth=auth,
        serials=["TEST123"],
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


def _echoing_client(sent_payloads: list[dict]) -> MagicMock:
    """A mock SunsynkClient whose async_get_settings echoes back the last
    write — write verification sees exactly what was just sent, so it
    always matches and the tests above stay focused on cache staleness
    rather than the verification fail-safe (covered separately below).
    """
    mock_client = MagicMock()

    async def _capture_write(session, serial, payload):
        sent_payloads.append(dict(payload))

    mock_client.async_write_settings = AsyncMock(side_effect=_capture_write)
    mock_client.async_get_settings = AsyncMock(
        side_effect=lambda session, serial: dict(sent_payloads[-1])
    )
    return mock_client


@pytest.mark.asyncio
async def test_second_write_in_a_burst_sees_the_first_writes_change(fake_coordinator):
    """The exact scenario that used to revert VirtualSlotScheduler writes."""
    sent_payloads: list[dict] = []
    mock_client = _echoing_client(sent_payloads)

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
    mock_client = _echoing_client([])

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
    mock_client = _echoing_client(sent_payloads)

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


# ── async_write_plant_price: plant_id cache-miss fallback (#20) ──────────────
#
# Regression coverage for "No plant found for inverter X" (#20): the cache's
# plant dict can be empty even though the account genuinely has a plant —
# e.g. right after startup, or a previous refresh's best-effort plant fetch
# failed. Failing immediately off a possibly-stale cache was too eager; a
# fresh inverter-info fetch should get one more chance before giving up.


@pytest.mark.asyncio
async def test_write_plant_price_falls_back_to_fresh_fetch_when_cache_has_no_plant(
    fake_coordinator,
):
    fake_coordinator.data["TEST123"]["plant"] = {}  # cache missed the plant lookup

    mock_client = MagicMock()
    mock_client.async_get_inverter_info = AsyncMock(
        return_value={"plant": {"id": 555}}
    )
    mock_client.async_get_plant_info = AsyncMock(
        return_value={"id": 555, "currency": {"id": "USD"}, "invest": 1000}
    )
    mock_client.async_set_plant_income = AsyncMock()

    with patch(
        "custom_components.sunsynk.coordinator.SunsynkClient", return_value=mock_client
    ):
        await SunsynkCoordinator.async_write_plant_price(fake_coordinator, "TEST123", 0.25)

    mock_client.async_get_inverter_info.assert_awaited_once()
    mock_client.async_set_plant_income.assert_awaited_once()
    args = mock_client.async_set_plant_income.call_args.args
    assert args[1] == "555"
    assert args[2]["charges"][0]["price"] == 0.25


@pytest.mark.asyncio
async def test_write_plant_price_raises_when_fresh_fetch_also_has_no_plant(
    fake_coordinator,
):
    from homeassistant.helpers.update_coordinator import UpdateFailed

    fake_coordinator.data["TEST123"]["plant"] = {}

    mock_client = MagicMock()
    mock_client.async_get_inverter_info = AsyncMock(return_value={})  # no "plant" key at all
    mock_client.async_set_plant_income = AsyncMock()

    with (
        patch("custom_components.sunsynk.coordinator.SunsynkClient", return_value=mock_client),
        pytest.raises(UpdateFailed, match="No plant found"),
    ):
        await SunsynkCoordinator.async_write_plant_price(fake_coordinator, "TEST123", 0.25)

    mock_client.async_set_plant_income.assert_not_awaited()


@pytest.mark.asyncio
async def test_write_plant_price_uses_cached_plant_id_without_refetching(fake_coordinator):
    fake_coordinator.data["TEST123"]["plant"] = {"id": 777}

    mock_client = MagicMock()
    mock_client.async_get_inverter_info = AsyncMock()
    mock_client.async_get_plant_info = AsyncMock(
        return_value={"id": 777, "currency": {"id": "USD"}, "invest": 0}
    )
    mock_client.async_set_plant_income = AsyncMock()

    with patch(
        "custom_components.sunsynk.coordinator.SunsynkClient", return_value=mock_client
    ):
        await SunsynkCoordinator.async_write_plant_price(fake_coordinator, "TEST123", 0.30)

    mock_client.async_get_inverter_info.assert_not_awaited()
    mock_client.async_set_plant_income.assert_awaited_once()


# ── async_write_setting: write verification fail-safe ────────────────────────


class TestValuesMatch:
    """`_values_match` has to tolerate the API's own quirks: it echoes
    booleans as "true"/"false" strings against the 1/0 ints we send, and
    numbers with inconsistent formatting (e.g. "90.0" for an int 90).
    """

    @pytest.mark.parametrize(
        ("sent", "actual"),
        [
            (1, "true"),
            (0, "false"),
            (1, "1"),
            (0, "0"),
            (90, "90.0"),
            (90, "90"),
            (90.0, 90),
            ("23:30", "23:30"),
            (0, 0.0),
        ],
    )
    def test_matching_values(self, sent, actual):
        assert SunsynkCoordinator._values_match(sent, actual) is True

    @pytest.mark.parametrize(
        ("sent", "actual"),
        [
            (1, "false"),
            (0, "true"),
            (90, "80"),
            ("23:30", "06:00"),
            (1, None),
        ],
    )
    def test_mismatching_values(self, sent, actual):
        assert SunsynkCoordinator._values_match(sent, actual) is False


@pytest.mark.asyncio
async def test_write_verification_clears_issue_on_match(fake_coordinator):
    mock_client = _echoing_client([])

    with (
        patch(
            "custom_components.sunsynk.coordinator.SunsynkClient", return_value=mock_client
        ),
        patch("custom_components.sunsynk.coordinator.ir") as mock_ir,
    ):
        await SunsynkCoordinator.async_write_setting(fake_coordinator, "TEST123", "time1on", 1)

    mock_ir.async_create_issue.assert_not_called()
    mock_ir.async_delete_issue.assert_any_call(
        fake_coordinator.hass,
        DOMAIN,
        "setting_write_mismatch_TEST123_time1on",
    )


@pytest.mark.asyncio
async def test_write_verification_raises_issue_on_mismatch(fake_coordinator):
    mock_client = MagicMock()
    mock_client.async_write_settings = AsyncMock()
    # The inverter reports the write never took — still "false" after we sent 1.
    mock_client.async_get_settings = AsyncMock(
        return_value={**fake_coordinator.data["TEST123"]["settings"], "time1on": "false"}
    )

    with (
        patch(
            "custom_components.sunsynk.coordinator.SunsynkClient", return_value=mock_client
        ),
        patch("custom_components.sunsynk.coordinator.ir") as mock_ir,
    ):
        await SunsynkCoordinator.async_write_setting(fake_coordinator, "TEST123", "time1on", 1)

    mock_ir.async_create_issue.assert_called_once()
    args, kwargs = mock_ir.async_create_issue.call_args
    assert args[:3] == (fake_coordinator.hass, DOMAIN, "setting_write_mismatch_TEST123_time1on")
    assert kwargs["translation_key"] == "setting_write_mismatch"
    assert kwargs["translation_placeholders"] == {
        "serial": "TEST123",
        "setting_key": "time1on",
        "expected": "1",
        "actual": "false",
    }


@pytest.mark.asyncio
async def test_write_verification_does_not_block_on_api_error(fake_coordinator):
    """If the confirming read itself fails, the write should still be
    treated as successful (the request_refresh below still runs) — a
    verification hiccup shouldn't turn into a raised exception on top of
    an otherwise-successful write.
    """
    mock_client = MagicMock()
    mock_client.async_write_settings = AsyncMock()
    mock_client.async_get_settings = AsyncMock(
        side_effect=SunsynkApiError("boom")
    )

    with (
        patch(
            "custom_components.sunsynk.coordinator.SunsynkClient", return_value=mock_client
        ),
        patch("custom_components.sunsynk.coordinator.ir") as mock_ir,
    ):
        await SunsynkCoordinator.async_write_setting(fake_coordinator, "TEST123", "time1on", 1)

    mock_ir.async_create_issue.assert_not_called()
    mock_ir.async_delete_issue.assert_not_called()
    fake_coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_write_verification_waits_before_reading_back(fake_coordinator):
    """Regression coverage for #21: a parallel/multi-inverter setup relays
    writes asynchronously — the API acknowledges before the command has
    actually propagated, so an immediate read-back raced ahead of it and
    flagged every write as a mismatch. Verification must wait first.
    """
    mock_client = _echoing_client([])

    with (
        patch("custom_components.sunsynk.coordinator.SunsynkClient", return_value=mock_client),
        patch("custom_components.sunsynk.coordinator.asyncio.sleep", AsyncMock()) as mock_sleep,
    ):
        await SunsynkCoordinator.async_write_setting(fake_coordinator, "TEST123", "time1on", 1)

    mock_sleep.assert_awaited_once()
    assert mock_sleep.call_args.args[0] > 0


# ── Parallel-inverter master redirect for battery settings (#21) ─────────────
#
# Regression coverage: a parallel/multi-inverter account had chargeCurrent
# and dischargeCurrent corrupted on BOTH units (0 on the slave, a wildly
# out-of-range 1040 on the master) after this integration wrote them to each
# configured serial independently. The reporter confirmed the Sunsynk portal
# itself only needs the master updated for a change to reach the whole
# parallel group. Battery settings writes to a parallel slave should now
# redirect to that group's master (found via `equipMode`: 0 = slave,
# 1 = master); everything else is unaffected.


def _parallel_coordinator() -> SimpleNamespace:
    auth = MagicMock()
    auth._api_server = "api.sunsynk.net"
    auth.async_get_token = AsyncMock(return_value="token")

    return SimpleNamespace(
        hass=MagicMock(),
        _auth=auth,
        serials=["SLAVE1", "MASTER1"],
        _async_get_session=AsyncMock(return_value=MagicMock()),
        async_request_refresh=AsyncMock(),
        data={
            "SLAVE1": {
                "inverter": {"parallel": 1, "equipMode": 0},
                "settings": {"sn": "SLAVE1", "dischargeCurrent": 0, "time1on": "false"},
            },
            "MASTER1": {
                "inverter": {"parallel": 1, "equipMode": 1},
                "settings": {"sn": "MASTER1", "dischargeCurrent": 1040, "time1on": "false"},
            },
        },
    )


class TestResolveParallelWriteTarget:
    def test_battery_setting_on_slave_redirects_to_master(self):
        coordinator = _parallel_coordinator()
        target = SunsynkCoordinator._resolve_parallel_write_target(
            coordinator, "SLAVE1", "dischargeCurrent"
        )
        assert target == "MASTER1"

    def test_battery_setting_on_master_stays_on_master(self):
        coordinator = _parallel_coordinator()
        target = SunsynkCoordinator._resolve_parallel_write_target(
            coordinator, "MASTER1", "dischargeCurrent"
        )
        assert target == "MASTER1"

    def test_non_battery_setting_is_never_redirected(self):
        """Only System Mode Timer slot settings verified fine independently
        per-unit in the reporter's diagnostics — no evidence they need the
        same redirect, so leave them alone."""
        coordinator = _parallel_coordinator()
        target = SunsynkCoordinator._resolve_parallel_write_target(
            coordinator, "SLAVE1", "time1on"
        )
        assert target == "SLAVE1"

    def test_non_parallel_setup_is_never_redirected(self, fake_coordinator):
        target = SunsynkCoordinator._resolve_parallel_write_target(
            fake_coordinator, "TEST123", "dischargeCurrent"
        )
        assert target == "TEST123"

    def test_no_master_found_falls_back_to_original_serial(self):
        """Defensive: if the group has no equipMode==1 unit for some reason
        (e.g. a momentary bad poll), don't silently write nowhere useful."""
        coordinator = _parallel_coordinator()
        coordinator.data["MASTER1"]["inverter"]["equipMode"] = 0
        target = SunsynkCoordinator._resolve_parallel_write_target(
            coordinator, "SLAVE1", "dischargeCurrent"
        )
        assert target == "SLAVE1"


@pytest.mark.asyncio
async def test_write_setting_redirects_battery_key_to_parallel_master():
    coordinator = _parallel_coordinator()
    sent_payloads: list[dict] = []
    mock_client = _echoing_client(sent_payloads)

    with patch(
        "custom_components.sunsynk.coordinator.SunsynkClient", return_value=mock_client
    ):
        await SunsynkCoordinator.async_write_setting(coordinator, "SLAVE1", "dischargeCurrent", 27)

    # The actual API write must have targeted the master's serial, not the
    # slave's — and the master's (not the slave's) cache reflects it.
    assert sent_payloads[0]["sn"] == "MASTER1"
    assert coordinator.data["MASTER1"]["settings"]["dischargeCurrent"] == 27
    assert coordinator.data["SLAVE1"]["settings"]["dischargeCurrent"] == 0

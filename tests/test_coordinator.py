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

import asyncio
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
def real_confirm():
    """Request the real background write confirmation in a test."""


@pytest.fixture(autouse=True)
def _confirmation_off_unless_requested(request):
    """Most tests are about the write itself, not its 60 s confirmation."""
    if "real_confirm" in request.fixturenames:
        yield
        return
    with patch.object(SunsynkCoordinator, "_async_confirm_write", AsyncMock()):
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


# ── settings-write payload scoping: a slot write must not touch other slots
# or unrelated global fields (#21) ────────────────────────────────────────
#
# A reporter on a parallel/dual-inverter system found that writing one
# physical slot's fields (time1on/cap1/sellTime1Pac/sellTime1/sellTime1on)
# used to resend all 36 SYSTEM_MODE_SETTING_KEYS fields — every other
# slot's cached values plus unrelated global settings — on every single
# field write. That (a) multiplied write volume enough to visibly desync
# master/slave (screen "flickering many times" per write), and (b)
# reintroduced a stale, chronologically-earlier start time from an
# untouched slot (e.g. slot 2's original 05:30 grid-charge start) into the
# payload, which silently corrupted the *targeted* slot's own end time
# (Sunsynk derives slot N's end from slot N+1's start). Slot fields now
# group only with their own slot's siblings.


@pytest.mark.asyncio
async def test_slot_write_payload_excludes_other_slots_fields(fake_coordinator):
    fake_coordinator.data["TEST123"]["settings"]["sellTime2"] = "05:30"
    fake_coordinator.data["TEST123"]["settings"]["cap2"] = "20"
    sent_payloads: list[dict] = []
    mock_client = _echoing_client(sent_payloads)

    with patch(
        "custom_components.sunsynk.coordinator.SunsynkClient", return_value=mock_client
    ):
        await SunsynkCoordinator.async_write_setting(fake_coordinator, "TEST123", "time1on", 1)

    assert "sellTime2" not in sent_payloads[0]
    assert "cap2" not in sent_payloads[0]


@pytest.mark.asyncio
async def test_slot_write_payload_still_includes_same_slot_siblings(fake_coordinator):
    """The reason these are grouped at all: sellTime{n}on silently failed to
    persist when sent alone, without its own slot's other fields (#21)."""
    sent_payloads: list[dict] = []
    mock_client = _echoing_client(sent_payloads)

    with patch(
        "custom_components.sunsynk.coordinator.SunsynkClient", return_value=mock_client
    ):
        await SunsynkCoordinator.async_write_setting(fake_coordinator, "TEST123", "time1on", 1)

    for key in ("cap1", "sellTime1Pac", "sellTime1", "time1on"):
        assert key in sent_payloads[0]


@pytest.mark.asyncio
async def test_global_system_mode_write_still_uses_full_group(fake_coordinator):
    """Non-slot System Mode Timer settings (solarSell, pvMaxLimit, ...) keep
    the original full-group write behavior — only slot-indexed fields were
    ever implicated in the desync/corruption reports."""
    fake_coordinator.data["TEST123"]["settings"]["pvMaxLimit"] = "6000"
    sent_payloads: list[dict] = []
    mock_client = _echoing_client(sent_payloads)

    with patch(
        "custom_components.sunsynk.coordinator.SunsynkClient", return_value=mock_client
    ):
        await SunsynkCoordinator.async_write_setting(fake_coordinator, "TEST123", "solarSell", 1)

    assert sent_payloads[0]["pvMaxLimit"] == "6000"


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


async def _settle_verification(coordinator) -> None:
    """Let background write confirmations finish (sleeps are patched out)."""
    await asyncio.gather(*list(coordinator._verify_tasks))


def _gated_sleep():
    """A patched sleep that holds background confirmation until released."""
    gate = asyncio.Event()

    async def _sleep(_seconds):
        await gate.wait()

    return gate, _sleep


def _client(read_backs) -> MagicMock:
    mock_client = MagicMock()
    mock_client.async_write_settings = AsyncMock()
    mock_client.async_get_settings = AsyncMock(side_effect=read_backs)
    return mock_client


@pytest.mark.asyncio
async def test_write_verification_clears_issue_on_match(fake_coordinator, real_confirm):
    mock_client = _echoing_client([])

    with (
        patch(
            "custom_components.sunsynk.coordinator.SunsynkClient", return_value=mock_client
        ),
        patch("custom_components.sunsynk.coordinator.ir") as mock_ir,
    ):
        await SunsynkCoordinator.async_write_setting(fake_coordinator, "TEST123", "time1on", 1)
        await _settle_verification(fake_coordinator)

    mock_ir.async_create_issue.assert_not_called()
    mock_ir.async_delete_issue.assert_called_once_with(
        fake_coordinator.hass, DOMAIN, "setting_write_mismatch_TEST123_time1on"
    )


@pytest.mark.asyncio
async def test_write_verification_raises_issue_only_after_last_offset(
    fake_coordinator, real_confirm,
):
    stale = {**fake_coordinator.data["TEST123"]["settings"], "time1on": "false"}
    mock_client = _client([stale] * 5)
    gate, gated_sleep = _gated_sleep()

    with (
        patch(
            "custom_components.sunsynk.coordinator.SunsynkClient", return_value=mock_client
        ),
        patch("custom_components.sunsynk.coordinator.ir") as mock_ir,
        patch("custom_components.sunsynk.coordinator.asyncio.sleep", gated_sleep),
    ):
        # The write itself returns straight away; confirmation is background.
        await SunsynkCoordinator.async_write_setting(fake_coordinator, "TEST123", "time1on", 1)
        assert fake_coordinator.data["TEST123"]["settings"]["time1on"] == 1
        mock_ir.async_create_issue.assert_not_called()
        gate.set()
        await _settle_verification(fake_coordinator)

    assert mock_client.async_get_settings.await_count == 5
    mock_ir.async_create_issue.assert_called_once()
    args, kwargs = mock_ir.async_create_issue.call_args
    assert args[:3] == (fake_coordinator.hass, DOMAIN, "setting_write_mismatch_TEST123_time1on")
    assert kwargs["translation_placeholders"] == {
        "serial": "TEST123",
        "setting_key": "time1on",
        "expected": "1",
        "actual": "false",
    }
    assert fake_coordinator.data["TEST123"]["settings"]["time1on"] == "false"


@pytest.mark.asyncio
async def test_slow_propagation_is_not_reported_as_a_failure(fake_coordinator, real_confirm):
    """#31: the inverter still reported the previous value seconds after
    every write, yet had applied it by the next one."""
    stale = {**fake_coordinator.data["TEST123"]["settings"], "time1on": "false"}
    current = {**stale, "time1on": "true"}
    mock_client = _client([stale, stale, current])

    with (
        patch(
            "custom_components.sunsynk.coordinator.SunsynkClient", return_value=mock_client
        ),
        patch("custom_components.sunsynk.coordinator.ir") as mock_ir,
        patch(
            "custom_components.sunsynk.coordinator.asyncio.sleep", AsyncMock()
        ) as mock_sleep,
    ):
        await SunsynkCoordinator.async_write_setting(fake_coordinator, "TEST123", "time1on", 1)
        await _settle_verification(fake_coordinator)

    assert mock_client.async_get_settings.await_count == 3
    assert [round(c.args[0]) for c in mock_sleep.await_args_list] == [5, 10, 15]
    mock_ir.async_create_issue.assert_not_called()
    mock_ir.async_delete_issue.assert_called_once()


@pytest.mark.asyncio
async def test_unconfirmed_value_survives_a_stale_poll(fake_coordinator, real_confirm):
    """#31: changing a slot's limit used to resend the slot's old start time
    when a poll had read the inverter before the new start time arrived."""
    sent_payloads: list[dict] = []
    mock_client = MagicMock()
    mock_client.async_write_settings = AsyncMock(
        side_effect=lambda session, serial, payload: sent_payloads.append(dict(payload))
    )
    _gate, blocking_sleep = _gated_sleep()

    with (
        patch(
            "custom_components.sunsynk.coordinator.SunsynkClient", return_value=mock_client
        ),
        patch("custom_components.sunsynk.coordinator.asyncio.sleep", blocking_sleep),
    ):
        await SunsynkCoordinator.async_write_setting(
            fake_coordinator, "TEST123", "sellTime1", "23:00"
        )
        polled = {"settings": {**fake_coordinator.data["TEST123"]["settings"], "sellTime1": "23:30"}}
        SunsynkCoordinator._overlay_unconfirmed_writes(fake_coordinator, "TEST123", polled)
        assert polled["settings"]["sellTime1"] == "23:00"
        fake_coordinator.data["TEST123"] = polled

        await SunsynkCoordinator.async_write_setting(fake_coordinator, "TEST123", "cap1", 90)
        await SunsynkCoordinator.async_close(
            SimpleNamespace(
                _verify_tasks=fake_coordinator._verify_tasks, _session=None
            )
        )

    assert sent_payloads[1]["cap1"] == 90
    assert sent_payloads[1]["sellTime1"] == "23:00"


def test_overlay_without_unconfirmed_writes_leaves_poll_untouched(fake_coordinator):
    polled = {"settings": {"cap1": "50"}}
    SunsynkCoordinator._overlay_unconfirmed_writes(fake_coordinator, "TEST123", polled)
    assert polled == {"settings": {"cap1": "50"}}


@pytest.mark.asyncio
async def test_superseded_write_stops_background_confirmation(fake_coordinator, real_confirm):
    mock_client = _client([])
    gate, gated_sleep = _gated_sleep()

    with (
        patch(
            "custom_components.sunsynk.coordinator.SunsynkClient", return_value=mock_client
        ),
        patch("custom_components.sunsynk.coordinator.ir") as mock_ir,
        patch("custom_components.sunsynk.coordinator.asyncio.sleep", gated_sleep),
    ):
        await SunsynkCoordinator.async_write_setting(fake_coordinator, "TEST123", "time1on", 1)
        fake_coordinator._unconfirmed_writes["TEST123"]["time1on"] = (999, 0)
        gate.set()
        await _settle_verification(fake_coordinator)

    mock_client.async_get_settings.assert_not_awaited()
    mock_ir.async_create_issue.assert_not_called()


@pytest.mark.asyncio
async def test_superseded_after_last_read_is_left_to_the_newer_write(fake_coordinator, real_confirm):
    stale = {**fake_coordinator.data["TEST123"]["settings"], "time1on": "false"}

    async def _stale_then_superseded(session, serial):
        if mock_client.async_get_settings.await_count == 5:
            fake_coordinator._unconfirmed_writes["TEST123"]["time1on"] = (999, 0)
        return stale

    mock_client = _client(_stale_then_superseded)

    with (
        patch(
            "custom_components.sunsynk.coordinator.SunsynkClient", return_value=mock_client
        ),
        patch("custom_components.sunsynk.coordinator.ir") as mock_ir,
    ):
        await SunsynkCoordinator.async_write_setting(fake_coordinator, "TEST123", "time1on", 1)
        await _settle_verification(fake_coordinator)

    mock_ir.async_create_issue.assert_not_called()
    assert fake_coordinator._unconfirmed_writes["TEST123"] == {"time1on": (999, 0)}


@pytest.mark.asyncio
async def test_write_verification_does_not_block_on_api_error(fake_coordinator, real_confirm):
    """A confirmation that can never read back must not claim a rejection."""
    mock_client = _client(SunsynkApiError("boom"))

    with (
        patch(
            "custom_components.sunsynk.coordinator.SunsynkClient", return_value=mock_client
        ),
        patch("custom_components.sunsynk.coordinator.ir") as mock_ir,
    ):
        await SunsynkCoordinator.async_write_setting(fake_coordinator, "TEST123", "time1on", 1)
        await _settle_verification(fake_coordinator)

    assert mock_client.async_get_settings.await_count == 5
    mock_ir.async_create_issue.assert_not_called()
    mock_ir.async_delete_issue.assert_not_called()
    assert fake_coordinator._unconfirmed_writes["TEST123"] == {}
    fake_coordinator.async_request_refresh.assert_awaited_once()


# ── Parallel-inverter master redirect for all settings (#21) ─────────────────
#
# Regression coverage: a parallel/multi-inverter account had chargeCurrent
# and dischargeCurrent corrupted on BOTH units (0 on the slave, a wildly
# out-of-range value on the master) after this integration wrote them to
# each configured serial independently. Originally redirected only battery
# settings, but the reporter later confirmed directly on the Sunsynk portal
# that a slave never independently keeps *any* setting — writing a slot's
# start time to the slave there too got silently reverted back to the
# master's value 10-15 seconds later. So every setting written to a
# parallel slave now redirects to that group's master (found via
# `equipMode`: 0 = slave, 1 = master); non-parallel accounts are unaffected.


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

    def test_time_slot_setting_on_slave_also_redirects_to_master(self):
        """Originally left unredirected — an earlier diagnostics dump made
        it look like these verified fine independently. Turned out to be a
        2-second verification window landing before a ~10-15s master->slave
        revert the reporter later confirmed directly on the Sunsynk portal
        (wrote to the slave there too, watched it snap back). Redirected
        now like every other setting."""
        coordinator = _parallel_coordinator()
        target = SunsynkCoordinator._resolve_parallel_write_target(
            coordinator, "SLAVE1", "time1on"
        )
        assert target == "MASTER1"

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


@pytest.mark.asyncio
async def test_write_setting_redirects_time_slot_key_to_parallel_master():
    """Same as above but for a System Mode Timer slot setting — these were
    originally left unredirected, then confirmed to need the same treatment
    (see TestResolveParallelWriteTarget's docstring for the story)."""
    coordinator = _parallel_coordinator()
    sent_payloads: list[dict] = []
    mock_client = _echoing_client(sent_payloads)

    with patch(
        "custom_components.sunsynk.coordinator.SunsynkClient", return_value=mock_client
    ):
        await SunsynkCoordinator.async_write_setting(coordinator, "SLAVE1", "time1on", 1)

    assert sent_payloads[0]["sn"] == "MASTER1"
    assert coordinator.data["MASTER1"]["settings"]["time1on"] == 1
    assert coordinator.data["SLAVE1"]["settings"]["time1on"] == "false"


# ── write_target_serials: dedup a parallel group down to its master ─────────
#
# Regression coverage: `async_write_setting` redirecting a slave's write to
# its master (above) stops the slave's own copy of a setting from being
# corrupted — but callers that loop "for serial in coordinator.serials" and
# call async_write_setting once per configured serial were still writing the
# same setting to the master twice per tick (once via the redirected slave
# call, once via the native master call). A second write landing right
# behind the first was, on its own, enough to make the master itself
# intermittently reject/revert one of them — confirmed by a reporter seeing
# fresh Repairs against the *master's* serial, not just the slave's, even
# after the redirect fix. write_target_serials lets write-issuing callers
# (Tariff Manager, Virtual Slot Scheduler) iterate a list with the slave
# already collapsed into its master, so each setting is written once.


class TestWriteTargetSerials:
    def _get(self, coordinator) -> list[str]:
        return SunsynkCoordinator.write_target_serials.fget(coordinator)

    def test_non_parallel_setup_returns_all_serials_unchanged(self, fake_coordinator):
        assert self._get(fake_coordinator) == ["TEST123"]

    def test_parallel_group_collapses_slave_into_master(self):
        coordinator = _parallel_coordinator()
        assert self._get(coordinator) == ["MASTER1"]

    def test_parallel_group_with_no_master_found_keeps_all_serials(self):
        """Defensive: don't silently drop a serial with nothing left to
        cover it (e.g. a momentary bad poll leaves equipMode unreadable)."""
        coordinator = _parallel_coordinator()
        coordinator.data["MASTER1"]["inverter"]["equipMode"] = 0
        assert self._get(coordinator) == ["SLAVE1", "MASTER1"]

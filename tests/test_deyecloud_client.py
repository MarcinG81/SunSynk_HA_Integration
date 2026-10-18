"""DeyeCloud OpenAPI client, against the protocol in Deye's own samples."""

from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest

from custom_components.sunsynk.api.client import (
    SunsynkApiError,
    SunsynkAuthenticationError,
)
from custom_components.sunsynk.deyecloud.client import (
    LATEST_BATCH,
    REGIONS,
    DeyeCloudApiError,
    DeyeCloudAuthError,
    DeyeCloudBusyError,
    DeyeCloudClient,
    DeyeCloudOrderError,
    DeyeCloudRateLimitError,
    hash_password,
    tou_time,
)
from tests.conftest import FakeResponse, fake_session

TOKEN_OK = {"code": "1000000", "success": True, "accessToken": "tok1", "expiresIn": 7200}


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _client(session, login="me@example.com", company_id=None, clock=None):
    return DeyeCloudClient(
        session,
        "eu",
        "APPID",
        "SECRET",
        login,
        hash_password("pw"),
        company_id=company_id,
        clock=clock or Clock(),
    )


def _ok(data=None, **extra):
    return FakeResponse({"code": 1000000, "success": True, "data": data, **extra})


# ── Helpers ─────────────────────────────────────────────────────────────────


def test_password_is_sent_as_its_sha256_digest():
    assert hash_password("pw") == hashlib.sha256(b"pw").hexdigest()


@pytest.mark.parametrize(
    ("value", "expected"),
    [("0130", "01:30"), ("130", "01:30"), ("1:5", "01:05"), ("23:59", "23:59"), (0, "00:00")],
)
def test_tou_times_become_hh_mm(value, expected):
    assert tou_time(value) == expected


@pytest.mark.parametrize("value", ["2400", "12:60", "ab"])
def test_impossible_tou_times_are_refused(value):
    with pytest.raises(ValueError):
        tou_time(value)


def test_unknown_region_is_refused():
    with pytest.raises(ValueError, match="region"):
        DeyeCloudClient(fake_session(), "mars", "a", "b", "c", "d")
    assert set(REGIONS) == {"eu", "us"}


def test_errors_are_the_integrations_own():
    assert issubclass(DeyeCloudApiError, SunsynkApiError)
    assert issubclass(DeyeCloudAuthError, SunsynkAuthenticationError)


# ── Login ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("login", "field"), [("me@example.com", "email"), ("solaruser", "username")]
)
async def test_login_sends_app_id_secret_and_hashed_password(login, field):
    session = fake_session(post=FakeResponse(TOKEN_OK))
    client = _client(session, login=login, company_id="77")

    assert await client.async_get_token() == "tok1"

    call = session.post.call_args
    assert call.args[0] == REGIONS["eu"] + "/account/token"
    assert call.kwargs["params"] == {"appId": "APPID"}
    assert call.kwargs["allow_redirects"] is False
    assert call.kwargs["json"] == {
        "appSecret": "SECRET",
        "password": hash_password("pw"),
        field: login,
        "companyId": "77",
    }


@pytest.mark.asyncio
async def test_token_is_reused_until_shortly_before_it_expires():
    clock = Clock()
    session = fake_session(
        post=[FakeResponse(TOKEN_OK), FakeResponse({**TOKEN_OK, "accessToken": "bearer tok2"})]
    )
    client = _client(session, clock=clock)

    assert await client.async_get_token() == "tok1"
    clock.now += 3000  # 7200 s token, refreshed an hour early
    assert await client.async_get_token() == "tok1"
    clock.now += 1000
    assert await client.async_get_token() == "tok2"


@pytest.mark.asyncio
async def test_missing_expiry_is_treated_as_a_day():
    clock = Clock()
    session = fake_session(post=FakeResponse({"code": 0, "accessToken": "t"}))
    client = _client(session, clock=clock)

    await client.async_get_token()

    clock.now += 20 * 3600
    assert await client.async_get_token() == "t"
    assert session.post.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "error"),
    [
        ({"code": 2101017, "msg": "wrong password"}, DeyeCloudAuthError),
        ({"code": 1, "msg": "Request too frequent, quota exceeded"}, DeyeCloudRateLimitError),
        ({"code": 0}, DeyeCloudAuthError),  # no token
    ],
)
async def test_refused_login(body, error):
    client = _client(fake_session(post=FakeResponse(body)))

    with pytest.raises(error):
        await client.async_get_token()


# ── Transport ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_requests_carry_the_token_and_return_data():
    session = fake_session(post=[FakeResponse(TOKEN_OK), _ok({"x": 1})])
    client = _client(session)

    assert await client.async_get_config("SN1", "system") == {"x": 1}

    call = session.post.call_args
    assert call.args[0] == REGIONS["eu"] + "/config/system"
    assert call.kwargs["headers"]["Authorization"] == "bearer tok1"
    assert call.kwargs["json"] == {"deviceSn": "SN1"}


@pytest.mark.asyncio
async def test_a_response_without_data_is_returned_whole():
    session = fake_session(post=[FakeResponse(TOKEN_OK), FakeResponse({"code": 0, "x": 2})])

    assert await _client(session).async_get_config("SN1", "tou") == {"code": 0, "x": 2}


@pytest.mark.asyncio
async def test_an_expired_token_is_replaced_once():
    session = fake_session(
        post=[
            FakeResponse(TOKEN_OK),
            FakeResponse({"code": 1002, "msg": "token expired"}),
            FakeResponse({**TOKEN_OK, "accessToken": "tok2"}),
            _ok({"ok": True}),
        ]
    )

    assert await _client(session).async_get_config("SN1", "battery") == {"ok": True}
    assert session.post.call_args.kwargs["headers"]["Authorization"] == "bearer tok2"


@pytest.mark.asyncio
async def test_a_login_refused_twice_is_an_auth_error():
    session = fake_session(
        post=[
            FakeResponse(TOKEN_OK),
            FakeResponse({"code": 1002, "msg": "token expired"}),
            FakeResponse(TOKEN_OK),
            FakeResponse({"code": 1002, "msg": "token expired"}),
        ]
    )

    with pytest.raises(DeyeCloudAuthError):
        await _client(session).async_get_config("SN1", "battery")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "error", "match"),
    [
        (FakeResponse({"code": 5, "msg": "Daily quota exceeded"}), DeyeCloudRateLimitError, "quota"),
        (FakeResponse({"code": 5, "msg": "no such device"}), DeyeCloudApiError, "no such device"),
        (FakeResponse({}, status=429), DeyeCloudRateLimitError, "429"),
        (FakeResponse({}, status=401), DeyeCloudAuthError, "401"),
        (FakeResponse({}, status=500), DeyeCloudApiError, "HTTP 500"),
        (FakeResponse({}, status=302), DeyeCloudApiError, "redirected"),
        (FakeResponse(["not", "a", "dict"]), DeyeCloudApiError, "Unexpected"),
    ],
)
async def test_failed_requests(response, error, match):
    session = fake_session(post=[FakeResponse(TOKEN_OK), response])

    with pytest.raises(error, match=match):
        await _client(session).async_get_config("SN1", "system")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raised", "match"),
    [(aiohttp.ClientConnectionError("down"), "Connection error"), (TimeoutError(), "Timed out")],
)
async def test_connection_problems_are_api_errors(raised, match):
    session = fake_session(post=[FakeResponse(TOKEN_OK), raised])

    with pytest.raises(DeyeCloudApiError, match=match):
        await _client(session).async_get_config("SN1", "system")


@pytest.mark.asyncio
async def test_unknown_config_group_is_refused():
    with pytest.raises(ValueError):
        await _client(fake_session()).async_get_config("SN1", "everything")


# ── Reading ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_inverters_are_listed_across_pages_with_their_station():
    page1 = _ok(
        {
            "total": 51,
            "stationList": [
                {"id": 1, "name": "Home", "deviceListItems": [{"deviceSn": "A"}, {"x": 1}]}
            ]
            + [{"id": 9, "name": "Empty", "deviceListItems": []}] * 49,
        }
    )
    page2 = _ok({"total": 51, "stationList": [{"id": 2, "name": "Barn", "deviceListItems": [{"deviceSn": "B"}]}]})
    session = fake_session(post=[FakeResponse(TOKEN_OK), page1, page2])

    inverters = await _client(session).async_list_inverters()

    assert [(i["deviceSn"], i["stationId"], i["stationName"]) for i in inverters] == [
        ("A", 1, "Home"),
        ("B", 2, "Barn"),
    ]
    assert session.post.call_args.kwargs["json"] == {"page": 2, "size": 50, "deviceType": "INVERTER"}


@pytest.mark.asyncio
async def test_latest_data_is_fetched_ten_inverters_at_a_time():
    serials = [f"SN{n}" for n in range(LATEST_BATCH + 2)]

    def batch(serial_list):
        return _ok(
            {
                "deviceDataList": [
                    {"deviceSn": sn, "dataList": [{"key": "SOC", "value": "50"}, {"value": 1}]}
                    for sn in serial_list
                ]
                + [{"dataList": []}]
            }
        )

    session = fake_session(
        post=[FakeResponse(TOKEN_OK), batch(serials[:LATEST_BATCH]), batch(serials[LATEST_BATCH:])]
    )

    latest = await _client(session).async_get_latest(serials)

    assert set(latest) == set(serials)
    assert latest["SN0"] == {"SOC": "50"}
    assert session.post.call_args.kwargs["json"] == {"deviceList": serials[LATEST_BATCH:]}


# ── Writing ─────────────────────────────────────────────────────────────────


def _order_session(*statuses):
    return fake_session(
        post=[FakeResponse(TOKEN_OK), _ok({"orderId": "o1"})],
        get=[_ok({"status": s}) for s in statuses],
    )


@pytest.mark.asyncio
async def test_a_change_waits_until_the_inverter_applies_it():
    session = _order_session(0, 100, 666)

    with patch("asyncio.sleep", AsyncMock()):
        result = await _client(session).async_set_work_mode("SN1", 2)

    assert result == {"status": 666}
    assert session.post.call_args.args[0].endswith("/order/sys/workMode/update")
    assert session.post.call_args.kwargs["json"] == {"deviceSn": "SN1", "workMode": "ZERO_EXPORT_TO_CT"}
    assert session.get.call_args.args[0].endswith("/order/o1")


@pytest.mark.asyncio
async def test_a_refused_change_is_an_order_error():
    with pytest.raises(DeyeCloudOrderError, match="didn't apply"):
        await _client(_order_session(500)).async_set_solar_sell("SN1", True)


@pytest.mark.asyncio
async def test_a_change_with_no_answer_times_out():
    clock = Clock()
    session = _order_session(*([100] * 5))
    client = _client(session, clock=clock)

    async def tick(_seconds):
        clock.now += 30

    with patch("asyncio.sleep", tick), pytest.raises(DeyeCloudOrderError, match="no answer"):
        await client.async_set_energy_pattern("SN1", 0)


@pytest.mark.asyncio
async def test_a_change_without_an_order_id_returns_at_once():
    session = fake_session(post=[FakeResponse(TOKEN_OK), _ok({"accepted": True})])

    assert await _client(session).async_set_max_sell_power("SN1", 5500) == {"accepted": True}
    assert session.post.call_args.kwargs["json"] == {
        "deviceSn": "SN1",
        "powerType": "MAX_SELL_POWER",
        "value": 5500,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("call", "path", "body"),
    [
        (("async_set_energy_pattern", 1), "/order/sys/energyPattern/update", {"energyPattern": "LOAD_FIRST"}),
        (("async_set_solar_sell", False), "/order/sys/solarSell/control", {"action": "off"}),
        (("async_set_battery_current", "charge", 120), "/order/battery/parameter/update", {"paramterType": "MAX_CHARGE_CURRENT", "value": 120}),
        (("async_set_battery_current", "discharge", 90), "/order/battery/parameter/update", {"paramterType": "MAX_DISCHARGE_CURRENT", "value": 90}),
        (
            ("async_set_tou_enabled", True),
            "/order/sys/tou/switch",
            {"action": "on", "days": ["SUNDAY", "MONDAY", "TUESDAY", "WEDNESDAY",
                                      "THURSDAY", "FRIDAY", "SATURDAY"]},
        ),
    ],
)
async def test_each_setting_goes_to_its_own_order(call, path, body):
    session = _order_session(666)

    await getattr(_client(session), call[0])("SN1", *call[1:])

    assert session.post.call_args.args[0].endswith(path)
    assert session.post.call_args.kwargs["json"] == {"deviceSn": "SN1", **body}


@pytest.mark.asyncio
async def test_the_whole_time_of_use_table_is_written_in_one_order():
    session = _order_session(666)
    slots = [
        {"time": "0130", "power": "5000", "soc": 30, "enableGridCharge": 1},
        *({"time": f"{h:02d}:00", "power": 0, "soc": 20} for h in (5, 9, 16, 19, 22)),
    ]

    await _client(session).async_set_tou("SN1", slots)

    body = session.post.call_args.kwargs["json"]
    assert body["deviceSn"] == "SN1"
    assert body["timeUseSettingItems"][0] == {
        "time": "01:30",
        "power": 5000,
        "soc": 30,
        "enableGridCharge": True,
        "enableSell": False,
        "enableGeneration": False,
    }
    assert [i["time"] for i in body["timeUseSettingItems"]] == [
        "01:30", "05:00", "09:00", "16:00", "19:00", "22:00"
    ]


@pytest.mark.asyncio
async def test_the_time_of_use_table_must_have_six_slots():
    with pytest.raises(ValueError, match="6 slots"):
        await _client(fake_session()).async_set_tou("SN1", [{"time": "00:00"}])


@pytest.mark.asyncio
async def test_a_response_without_a_code_is_taken_as_success():
    session = fake_session(post=[FakeResponse(TOKEN_OK), FakeResponse({"data": {"y": 3}})])

    assert await _client(session).async_get_config("SN1", "system") == {"y": 3}


@pytest.mark.asyncio
async def test_an_unreadable_order_status_is_an_order_error():
    with pytest.raises(DeyeCloudOrderError, match="status None"):
        await _client(_order_session("done?")).async_set_tou_enabled("SN1", False)



@pytest.mark.asyncio
async def test_a_token_refused_by_message_only_is_replaced_once():
    """An expired token can come back as HTTP 200 with just a message."""
    session = fake_session(
        post=[
            FakeResponse(TOKEN_OK),
            FakeResponse({"code": "9", "success": False, "msg": "auth invalid token"}),
            FakeResponse({**TOKEN_OK, "accessToken": "tok2"}),
            _ok({"ok": 1}),
        ]
    )

    assert await _client(session).async_get_config("SN1", "system") == {"ok": 1}


@pytest.mark.asyncio
async def test_a_busy_inverter_is_a_retryable_error():
    session = fake_session(
        post=[FakeResponse(TOKEN_OK), FakeResponse({"code": "2104004", "msg": "command concurrent running"})]
    )

    with pytest.raises(DeyeCloudBusyError, match="concurrent"):
        await _client(session).async_set_solar_sell("SN1", True)


@pytest.mark.asyncio
async def test_an_order_waits_through_a_firmware_upgrade():
    with patch("asyncio.sleep", AsyncMock()):
        result = await _client(_order_session(300, 666)).async_set_solar_sell("SN1", True)

    assert result == {"status": 666}


@pytest.mark.asyncio
async def test_a_failed_order_says_why():
    session = fake_session(
        post=[FakeResponse(TOKEN_OK), _ok({"orderId": "o1"})],
        get=[_ok({"status": 500, "error": "501 Conflicting Command"})],
    )

    with pytest.raises(DeyeCloudOrderError, match="Conflicting Command"):
        await _client(session).async_set_solar_sell("SN1", True)


@pytest.mark.asyncio
async def test_turning_the_timer_off_sends_no_days():
    session = _order_session(666)

    await _client(session).async_set_tou_enabled("SN1", False)

    assert session.post.call_args.kwargs["json"] == {"deviceSn": "SN1", "action": "off"}

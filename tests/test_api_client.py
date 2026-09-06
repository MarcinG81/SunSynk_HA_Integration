"""Tests for SunsynkClient (custom_components/sunsynk/api/client.py)."""
from __future__ import annotations

import aiohttp
import pytest

from custom_components.sunsynk.api.client import SunsynkApiError, SunsynkClient
from tests.conftest import FakeResponse, fake_session


@pytest.fixture
def client() -> SunsynkClient:
    return SunsynkClient("api.sunsynk.net", "test-token")


def test_headers_include_bearer_token(client: SunsynkClient):
    headers = client._headers()
    assert headers["Authorization"] == "Bearer test-token"
    assert headers["Content-Type"] == "application/json"


class TestGet:
    @pytest.mark.asyncio
    async def test_returns_data_field_on_success(self, client: SunsynkClient):
        session = fake_session(get=FakeResponse({"msg": "Success", "data": {"foo": "bar"}}))
        result = await client._get(session, "https://api.sunsynk.net/x")
        assert result == {"foo": "bar"}

    @pytest.mark.asyncio
    async def test_returns_empty_dict_when_data_field_missing(self, client: SunsynkClient):
        session = fake_session(get=FakeResponse({"msg": "Success"}))
        result = await client._get(session, "https://api.sunsynk.net/x")
        assert result == {}

    @pytest.mark.asyncio
    async def test_raises_on_non_success_msg(self, client: SunsynkClient):
        session = fake_session(get=FakeResponse({"msg": "Invalid token", "data": {}}))
        with pytest.raises(SunsynkApiError, match="Invalid token"):
            await client._get(session, "https://api.sunsynk.net/x")

    @pytest.mark.asyncio
    async def test_raises_sunsynk_error_on_http_error_status(self, client: SunsynkClient):
        session = fake_session(get=FakeResponse({}, status=500))
        with pytest.raises(SunsynkApiError, match="HTTP 500"):
            await client._get(session, "https://api.sunsynk.net/x")

    @pytest.mark.asyncio
    async def test_raises_sunsynk_error_on_connection_error(self, client: SunsynkClient):
        session = fake_session()
        session.get.side_effect = aiohttp.ClientConnectionError("boom")
        with pytest.raises(SunsynkApiError, match="Connection error"):
            await client._get(session, "https://api.sunsynk.net/x")


class TestPost:
    @pytest.mark.asyncio
    async def test_returns_data_field_on_success(self, client: SunsynkClient):
        session = fake_session(post=FakeResponse({"msg": "Success", "data": {"ok": True}}))
        result = await client._post(session, "https://api.sunsynk.net/x", {"a": 1})
        assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_raises_on_non_success_msg(self, client: SunsynkClient):
        session = fake_session(post=FakeResponse({"msg": "Failed", "data": {}}))
        with pytest.raises(SunsynkApiError, match="Failed"):
            await client._post(session, "https://api.sunsynk.net/x", {})


class TestEndpointUrls:
    @pytest.mark.asyncio
    async def test_get_inverter_info_url(self, client: SunsynkClient):
        session = fake_session(get=FakeResponse({"msg": "Success", "data": {}}))
        await client.async_get_inverter_info(session, "SN1")
        session.get.assert_called_once()
        assert session.get.call_args.args[0] == "https://api.sunsynk.net/api/v1/inverter/SN1"

    @pytest.mark.asyncio
    async def test_get_pv_data_url(self, client: SunsynkClient):
        session = fake_session(get=FakeResponse({"msg": "Success", "data": {}}))
        await client.async_get_pv_data(session, "SN1")
        assert session.get.call_args.args[0] == (
            "https://api.sunsynk.net/api/v1/inverter/SN1/realtime/input"
        )

    @pytest.mark.asyncio
    async def test_get_grid_data_url_and_params(self, client: SunsynkClient):
        session = fake_session(get=FakeResponse({"msg": "Success", "data": {}}))
        await client.async_get_grid_data(session, "SN1")
        assert session.get.call_args.args[0] == (
            "https://api.sunsynk.net/api/v1/inverter/grid/SN1/realtime"
        )
        assert session.get.call_args.kwargs["params"] == {"sn": "SN1"}

    @pytest.mark.asyncio
    async def test_get_battery_data_url_and_params(self, client: SunsynkClient):
        session = fake_session(get=FakeResponse({"msg": "Success", "data": {}}))
        await client.async_get_battery_data(session, "SN1")
        assert session.get.call_args.args[0] == (
            "https://api.sunsynk.net/api/v1/inverter/battery/SN1/realtime"
        )
        assert session.get.call_args.kwargs["params"] == {"sn": "SN1", "lan": "en"}

    @pytest.mark.asyncio
    async def test_get_settings_url(self, client: SunsynkClient):
        session = fake_session(get=FakeResponse({"msg": "Success", "data": {}}))
        await client.async_get_settings(session, "SN1")
        assert session.get.call_args.args[0] == (
            "https://api.sunsynk.net/api/v1/common/setting/SN1/read"
        )

    @pytest.mark.asyncio
    async def test_write_settings_url_and_payload(self, client: SunsynkClient):
        session = fake_session(post=FakeResponse({"msg": "Success", "data": {}}))
        await client.async_write_settings(session, "SN1", {"chargeCurrent": 100})
        assert session.post.call_args.args[0] == (
            "https://api.sunsynk.net/api/v1/common/setting/SN1/set"
        )
        assert session.post.call_args.kwargs["json"] == {"chargeCurrent": 100}

    @pytest.mark.asyncio
    async def test_get_plant_info_url(self, client: SunsynkClient):
        session = fake_session(get=FakeResponse({"msg": "Success", "data": {}}))
        await client.async_get_plant_info(session, "42")
        assert session.get.call_args.args[0] == "https://api.sunsynk.net/api/v1/plant/42"

    @pytest.mark.asyncio
    async def test_set_plant_income_url(self, client: SunsynkClient):
        session = fake_session(post=FakeResponse({"msg": "Success", "data": {}}))
        await client.async_set_plant_income(session, "42", {"price": 1})
        assert session.post.call_args.args[0] == (
            "https://api.sunsynk.net/api/v1/plant/42/income"
        )


class TestGetTempData:
    @pytest.mark.asyncio
    async def test_extracts_dc_and_igbt_temps(self, client: SunsynkClient):
        raw = {
            "msg": "Success",
            "data": {
                "infos": [
                    {"label": "DC Temp", "records": [{"value": 1}, {"value": 35.2}]},
                    {"label": "AC Temp", "records": [{"value": 40.1}]},
                ]
            },
        }
        session = fake_session(get=FakeResponse(raw))
        result = await client.async_get_temp_data(session, "SN1")
        assert result == {"dc_temp": 35.2, "igbt_temp": 40.1}

    @pytest.mark.asyncio
    async def test_skips_labels_with_no_records(self, client: SunsynkClient):
        raw = {"msg": "Success", "data": {"infos": [{"label": "DC Temp", "records": []}]}}
        session = fake_session(get=FakeResponse(raw))
        result = await client.async_get_temp_data(session, "SN1")
        assert result == {}

    @pytest.mark.asyncio
    async def test_empty_infos_gives_empty_result(self, client: SunsynkClient):
        session = fake_session(get=FakeResponse({"msg": "Success", "data": {}}))
        result = await client.async_get_temp_data(session, "SN1")
        assert result == {}


class TestFetchAll:
    @pytest.mark.asyncio
    async def test_merges_all_endpoints_under_expected_keys(self, client: SunsynkClient):
        ok = FakeResponse({"msg": "Success", "data": {"x": 1}})
        # inverter, pv, grid, battery, load, output each make one GET call;
        # temp makes its own GET (7 GETs), settings makes an 8th.
        session = fake_session(get=[ok, ok, ok, ok, ok, ok, ok, ok])
        result = await client.async_fetch_all(session, "SN1")
        assert set(result.keys()) == {
            "inverter", "pv", "grid", "battery", "load", "output", "temp", "settings",
        }

    @pytest.mark.asyncio
    async def test_one_endpoint_failing_yields_empty_dict_for_it_only(
        self, client: SunsynkClient
    ):
        ok = FakeResponse({"msg": "Success", "data": {"x": 1}})
        # asyncio.gather preserves call order: inverter, pv, grid, battery,
        # load, output, temp, settings. Make "grid" (3rd) fail.
        failing = FakeResponse({"msg": "Success", "data": {}}, status=500)
        session = fake_session(get=[ok, ok, failing, ok, ok, ok, ok, ok])
        result = await client.async_fetch_all(session, "SN1")
        assert result["grid"] == {}
        assert result["inverter"] == {"x": 1}
        assert result["pv"] == {"x": 1}

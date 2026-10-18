"""The Deye Cloud probe masks identifying details and keeps the data."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from scripts.deyecloud_probe import probe, redact


def test_identifying_fields_are_masked_and_measurements_kept():
    report = redact(
        {
            "inverters": [
                {"deviceSn": "2301316667", "stationId": 1234567, "stationName": "My Home",
                 "deviceType": "INVERTER"},
            ],
            "devices": {
                "x": {
                    "latest": {"SOC": "64", "CumulativeGridFeedIn": "1234.5",
                               "BatteryVoltage": "52.1"},
                    "system": {"workMode": "SELLING_FIRST", "lat": 51.5, "longitude": -0.1,
                               "email": "me@example.com", "sn": "AB12"},
                }
            },
        }
    )

    inverter = report["inverters"][0]
    assert inverter["deviceSn"] == "23******67"
    assert inverter["stationId"] == "12***67"
    assert inverter["stationName"] == "My***me"
    assert inverter["deviceType"] == "INVERTER"
    device = report["devices"]["x"]
    assert device["latest"] == {"SOC": "64", "CumulativeGridFeedIn": "1234.5",
                                "BatteryVoltage": "52.1"}
    assert device["system"]["workMode"] == "SELLING_FIRST"
    assert device["system"]["lat"] == "****"
    assert device["system"]["longitude"] == "****"
    assert device["system"]["email"] == "me**********om"
    assert device["system"]["sn"] == "****"


def test_probe_logs_in_reads_everything_and_masks_it():
    calls = []

    def fake_post(url, body, token=None):
        calls.append((url.split("/v1.0")[1], body, token))
        path = url.split("/v1.0")[1]
        if path.startswith("/account/token"):
            return {"code": "1000000", "accessToken": "bearer T"}
        if path == "/station/listWithDevice":
            return {"code": 1000000, "data": {"stationList": [
                {"id": 99, "name": "Home", "deviceListItems": [{"deviceSn": "SN000001"}]}]}}
        if path == "/device/latest":
            return {"code": 0, "data": {"deviceDataList": [
                {"deviceSn": "SN000001", "dataList": [{"key": "SOC", "value": "50"}]}]}}
        if path == "/config/battery":
            raise OSError("not permitted")
        if path == "/config/tou":
            return {"code": 5, "msg": "no permission"}
        return {"code": 0, "data": {"workMode": "SELLING_FIRST"}}

    with patch("scripts.deyecloud_probe.post", side_effect=fake_post):
        report = probe("eu", "APP", "SECRET", "me@example.com", "pw")

    login = calls[0]
    assert login[0] == "/account/token?appId=APP"
    assert login[1]["email"] == "me@example.com"
    assert login[1]["password"] != "pw"
    assert all(token == "T" for _, _, token in calls[1:])
    assert "SN000001" not in str(report)
    device = report["devices"]["inverter_1"]
    assert device["latest"] == [{"key": "SOC", "value": "50"}]
    assert device["system"] == {"workMode": "SELLING_FIRST"}
    assert device["battery"] == {"error": "not permitted"}
    assert device["tou"]["error"]["msg"] == "no permission"


def test_a_refused_login_stops_the_probe():
    with patch("scripts.deyecloud_probe.post", return_value={"code": 2101017, "msg": "bad"}):
        with pytest.raises(SystemExit, match="Login failed: bad"):
            probe("us", "APP", "S", "solaruser", "pw", company_id="7")


def test_an_unknown_app_id_says_which_data_centre_knows_it():
    def fake_post(url, body, token=None):
        if url.startswith("https://us1"):
            return {"code": "2101019", "msg": "auth appSecret error"}
        return {"code": "2101022", "msg": "auth appId not found"}

    with patch("scripts.deyecloud_probe.post", side_effect=fake_post):
        with pytest.raises(SystemExit) as err:
            probe("eu", " 202310190001 ", "secret", "me@example.com", "pw")

    assert "try --region us" in str(err.value)
    assert "202...001 (12 characters)" in str(err.value)


def test_an_app_id_no_data_centre_knows_is_reported():
    with patch(
        "scripts.deyecloud_probe.post",
        return_value={"code": "2101022", "msg": "auth appId not found"},
    ):
        with pytest.raises(SystemExit) as err:
            probe("eu", "202310190001", "secret", "me@example.com", "pw")

    assert "not known in any data centre" in str(err.value)

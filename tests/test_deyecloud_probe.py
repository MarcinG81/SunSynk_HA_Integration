"""The Deye Cloud probe masks identifying details and keeps the data."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

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


@pytest.mark.asyncio
async def test_probe_reads_everything_and_reports_a_failed_group():
    client = MagicMock()
    client.async_list_inverters = AsyncMock(return_value=[{"deviceSn": "SN000001"}])
    client.async_get_latest = AsyncMock(return_value={"SN000001": {"SOC": "50"}})

    async def config(serial, kind):
        if kind == "battery":
            raise RuntimeError("not permitted")
        return {"kind": kind}

    client.async_get_config = AsyncMock(side_effect=config)
    args = SimpleNamespace(region="eu", app_id="A", login="l", company_id=None, limit=3)

    with patch("scripts.deyecloud_probe.DeyeCloudClient", return_value=client):
        report = await probe(args, "secret", "pw")

    device = next(iter(report["devices"].values()))
    assert device["latest"] == {"SOC": "50"}
    assert device["system"] == {"kind": "system"}
    assert device["battery"] == {"error": "not permitted"}

"""Each Sunsynk battery bank's and pack's own readings (#44).

The sunsynk.net battery page lists a bank (`<inverter serial>M01`) and reads
the packs behind it from the bank's `slave` list; each has a `realtime`
reading with its own SOC. The readings below are a two-pack bank's, as
reported on #44 (serials replaced).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.helpers.entity import EntityCategory

from custom_components.sunsynk.api.client import SunsynkApiError, SunsynkClient
from custom_components.sunsynk.coordinator import SunsynkCoordinator
from custom_components.sunsynk.diagnostics import _TO_REDACT
from custom_components.sunsynk.sensor import SunsynkSensor, _build_dynamic_descriptions
from tests.conftest import FakeResponse, fake_session

_BANK_READING = {
    "power": "-415",
    "current": "-8.0",
    "voltage": "51.9",
    "soc": "20.0",
    "soh": "97.0",
    "temp": 17.0,
    "aveTemp": None,
    "cycleNumber": None,
    "chargeCurrentLimit": 124.0,
    "cellMaxVolt": None,
    "cellMinVolt": None,
    "batteryFault1": "0",
    "batteryFault2": "0",
    "batteryWarning1": "0",
    "batteryWarning2": "0",
    "packSN": None,
}
_PACK_READING = {
    "power": "5",
    "current": "0.1",
    "voltage": "51.0",
    "soc": "17.9",
    "soh": "97.6",
    "temp": None,
    "aveTemp": 17.0,
    "cycleNumber": 489,
    "cellMaxVolt": 3.214,
    "cellMinVolt": 3.187,
    "batteryFault1": "0",
    "batteryFault2": "0",
    "batteryWarning1": "64",
    "batteryWarning2": "0",
    "packSN": None,
}


def _ok(data):
    return FakeResponse({"code": 0, "msg": "Success", "data": data})


@pytest.fixture
def client() -> SunsynkClient:
    return SunsynkClient("api.sunsynk.net", "test-token")


class TestClient:
    async def test_a_bank_and_the_packs_behind_it(self, client):
        session = fake_session(
            get=[
                _ok(
                    {
                        "total": 1,
                        "infos": [
                            {
                                "id": 12593,
                                "sn": "INV1M01",
                                "invSn": "INV1",
                                "gsn": "GSN1",
                                "packageNum": 2,
                                "hasSlave": True,
                            }
                        ],
                    }
                ),
                _ok([{"sn": "PACK000000000002", "status": 1}]),
            ]
        )
        packs = await client.async_get_battery_packs(session, "INV1")
        assert packs == [
            {"sn": "INV1M01", "gsn": "GSN1"},
            {"sn": "PACK000000000002", "gsn": "GSN1"},
        ]
        listing, slaves = session.get.call_args_list
        assert listing.args[0] == "https://api.sunsynk.net/api/v1/batteries"
        assert listing.kwargs["params"] == {
            "pageNumber": 1,
            "pageSize": 10,
            "status": -1,
            "invSn": "INV1",
        }
        assert slaves.args[0] == "https://api.sunsynk.net/api/v1/batteries/12593/slave"
        assert slaves.kwargs["params"] == {"id": 12593}

    async def test_a_bank_without_packs_and_others_left_out(self, client):
        session = fake_session(
            get=_ok(
                {
                    "infos": [
                        {"id": 1, "sn": "INV1M01", "packageNum": "1", "hasSlave": None},
                        {"id": 2, "sn": "INV2M01", "invSn": "INV2", "hasSlave": True},
                        {"id": 3, "sn": "", "hasSlave": True},
                        "not a battery",
                        {"sn": "INV1M02", "packageNum": "bad", "hasSlave": True},
                    ]
                }
            )
        )
        packs = await client.async_get_battery_packs(session, "INV1")
        assert packs == [{"sn": "INV1M01", "gsn": ""}, {"sn": "INV1M02", "gsn": ""}]
        assert session.get.call_count == 1

    async def test_a_slave_list_that_is_not_a_list(self, client):
        session = fake_session(
            get=[
                _ok({"infos": [{"id": 1, "sn": "INV1M01", "packageNum": 2}]}),
                _ok({}),
            ]
        )
        assert await client.async_get_battery_packs(session, "INV1") == [
            {"sn": "INV1M01", "gsn": ""}
        ]

    async def test_no_batteries_listed(self, client):
        session = fake_session(get=_ok({"infos": None}))
        assert await client.async_get_battery_packs(session, "INV1") == []

    async def test_a_pack_reading(self, client):
        session = fake_session(get=_ok(_PACK_READING))
        reading = await client.async_get_battery_pack_realtime(
            session, "INV1", {"sn": "PACK 2", "gsn": "GSN1"}
        )
        assert reading == _PACK_READING
        assert session.get.call_args.args[0] == (
            "https://api.sunsynk.net/api/v1/battery/PACK%202/realtime"
        )
        assert session.get.call_args.kwargs["params"] == {
            "sn": "PACK 2",
            "invSn": "INV1",
            "gsn": "GSN1",
        }

    async def test_a_pack_reading_without_a_gateway(self, client):
        session = fake_session(get=_ok(_BANK_READING))
        await client.async_get_battery_pack_realtime(
            session, "INV1", {"sn": "INV1M01", "gsn": ""}
        )
        assert session.get.call_args.kwargs["params"] == {"sn": "INV1M01", "invSn": "INV1"}


_PACKS = [{"sn": "INV1M01", "gsn": "G"}, {"sn": "PACK2", "gsn": "G"}]
_NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)


def _coordinator(data=None) -> SimpleNamespace:
    return SimpleNamespace(data=data)


def _client(packs=_PACKS) -> MagicMock:
    client = MagicMock()
    client.async_get_battery_packs = AsyncMock(return_value=packs)
    client.async_get_battery_pack_realtime = AsyncMock(
        side_effect=lambda session, serial, pack: (
            _BANK_READING if pack["sn"] == "INV1M01" else _PACK_READING
        )
    )
    return client


async def _read(coordinator, client, now=_NOW):
    with patch("custom_components.sunsynk.coordinator.dt_util.utcnow", return_value=now):
        return await SunsynkCoordinator._async_battery_packs(
            coordinator, client, MagicMock(), "INV1"
        )


class TestCoordinator:
    async def test_every_pack_read_and_the_list_kept_for_an_hour(self):
        coordinator = _coordinator()
        client = _client()
        assert await _read(coordinator, client) == {
            "packs": [_BANK_READING, _PACK_READING]
        }
        await _read(coordinator, client, _NOW + timedelta(minutes=59))
        assert client.async_get_battery_packs.await_count == 1
        assert client.async_get_battery_pack_realtime.await_count == 4
        await _read(coordinator, client, _NOW + timedelta(hours=1))
        assert client.async_get_battery_packs.await_count == 2

    async def test_no_batteries_listed(self):
        assert await _read(_coordinator(), _client(packs=[])) == {}

    async def test_a_failed_list_is_not_asked_for_again_for_an_hour(self):
        coordinator = _coordinator()
        client = _client()
        client.async_get_battery_packs.side_effect = SunsynkApiError("down")
        assert await _read(coordinator, client) == {}
        await _read(coordinator, client, _NOW + timedelta(minutes=30))
        assert client.async_get_battery_packs.await_count == 1

    async def test_a_failed_list_keeps_the_last_one(self):
        coordinator = _coordinator()
        client = _client()
        await _read(coordinator, client)
        client.async_get_battery_packs.side_effect = SunsynkApiError("down")
        assert await _read(coordinator, client, _NOW + timedelta(hours=2)) == {
            "packs": [_BANK_READING, _PACK_READING]
        }

    async def test_a_failed_reading_keeps_the_last_readings(self):
        last = {"packs": [_BANK_READING]}
        coordinator = _coordinator({"INV1": {"packs": last}})
        client = _client()
        client.async_get_battery_pack_realtime.side_effect = SunsynkApiError("down")
        assert await _read(coordinator, client) == last

    async def test_a_poll_reads_the_packs(self):
        """The packs are read with the rest of the inverter's data."""
        client = _client()
        client.async_fetch_all = AsyncMock(return_value={"inverter": {}})
        coordinator = SimpleNamespace(
            hass=MagicMock(),
            _entry_id="e",
            _auth=SimpleNamespace(
                _api_server="api.sunsynk.net",
                async_get_token=AsyncMock(return_value="token"),
            ),
            serials=["INV1"],
            data=None,
            _async_get_session=AsyncMock(return_value=MagicMock()),
        )
        with (
            patch("custom_components.sunsynk.coordinator.SunsynkClient", return_value=client),
            patch("custom_components.sunsynk.coordinator.ir"),
        ):
            result = await SunsynkCoordinator._async_update_data(coordinator)
        assert result["INV1"]["packs"] == {"packs": [_BANK_READING, _PACK_READING]}


def _sensors(packs) -> dict:
    coordinator = SimpleNamespace(data={"INV1": {"packs": {"packs": packs}}})
    return {
        desc.key: desc
        for desc in _build_dynamic_descriptions(coordinator, "INV1")
        if desc.key.startswith("battery_pack")
    }


class TestSensors:
    def test_each_pack_gets_the_sensors_it_reports(self):
        sensors = _sensors([_BANK_READING, _PACK_READING])
        assert sorted(sensors) == sorted(
            [
                "battery_pack1_soc",
                "battery_pack1_soh",
                "battery_pack1_voltage",
                "battery_pack1_current",
                "battery_pack1_power",
                "battery_pack1_temp",
                "battery_pack1_bms_warning_1",
                "battery_pack1_bms_warning_2",
                "battery_pack1_bms_fault_1",
                "battery_pack1_bms_fault_2",
                "battery_pack2_soc",
                "battery_pack2_soh",
                "battery_pack2_voltage",
                "battery_pack2_current",
                "battery_pack2_power",
                "battery_pack2_temp",
                "battery_pack2_cycles",
                "battery_pack2_cell_voltage_max",
                "battery_pack2_cell_voltage_min",
                "battery_pack2_cell_voltage_difference",
                "battery_pack2_bms_warning_1",
                "battery_pack2_bms_warning_2",
                "battery_pack2_bms_fault_1",
                "battery_pack2_bms_fault_2",
            ]
        )
        assert sensors["battery_pack2_soc"].name == "Battery Pack 2 SOC"
        assert sensors["battery_pack2_soc"].native_unit_of_measurement == "%"
        assert sensors["battery_pack2_voltage"].suggested_display_precision == 1
        assert sensors["battery_pack2_cycles"].suggested_display_precision == 0
        assert sensors["battery_pack2_soh"].icon == "mdi:battery-heart-variant"

    def test_cell_voltages_and_bms_codes_are_diagnostics(self):
        sensors = _sensors([_BANK_READING, _PACK_READING])
        cell = sensors["battery_pack2_cell_voltage_difference"]
        assert cell.name == "Battery Pack 2 Cell Voltage Difference"
        assert cell.native_unit_of_measurement == "V"
        assert cell.suggested_display_precision == 3
        assert cell.entity_category == EntityCategory.DIAGNOSTIC
        assert sensors["battery_pack2_cell_voltage_max"].state_class == "measurement"
        code = sensors["battery_pack2_bms_warning_1"]
        assert code.name == "Battery Pack 2 BMS Warning 1"
        assert code.state_class is None
        assert code.entity_category == EntityCategory.DIAGNOSTIC

    def test_a_difference_needs_both_cell_voltages(self):
        sensors = _sensors([{**_PACK_READING, "cellMinVolt": None}])
        assert "battery_pack1_cell_voltage_max" in sensors
        assert "battery_pack1_cell_voltage_difference" not in sensors

    @pytest.mark.parametrize("reading", [None, "--"])
    def test_a_difference_without_a_reading_is_unknown(self, reading):
        coordinator = SimpleNamespace(
            data={"INV1": {"packs": {"packs": [_PACK_READING]}}}
        )
        sensor = next(
            SunsynkSensor(coordinator, "INV1", desc, MagicMock())
            for desc in _build_dynamic_descriptions(coordinator, "INV1")
            if desc.key == "battery_pack1_cell_voltage_difference"
        )
        coordinator.data["INV1"]["packs"]["packs"] = [
            {**_PACK_READING, "cellMinVolt": reading}
        ]
        assert sensor.native_value is None

    def test_the_readings(self):
        coordinator = SimpleNamespace(
            data={"INV1": {"packs": {"packs": [_BANK_READING, _PACK_READING]}}}
        )
        sensors = {
            desc.key: SunsynkSensor(coordinator, "INV1", desc, MagicMock())
            for desc in _build_dynamic_descriptions(coordinator, "INV1")
            if desc.key.startswith("battery_pack")
        }
        assert sensors["battery_pack1_soc"].native_value == 20.0
        assert sensors["battery_pack2_soc"].native_value == 17.9
        assert sensors["battery_pack1_power"].native_value == -415.0
        assert sensors["battery_pack1_temp"].native_value == 17.0
        assert sensors["battery_pack2_temp"].native_value == 17.0
        assert sensors["battery_pack2_cycles"].native_value == 489.0
        assert sensors["battery_pack2_cell_voltage_max"].native_value == 3.214
        assert sensors["battery_pack2_cell_voltage_min"].native_value == 3.187
        assert sensors["battery_pack2_cell_voltage_difference"].native_value == 0.027
        assert sensors["battery_pack2_bms_warning_1"].native_value == "64"
        assert sensors["battery_pack2_bms_fault_1"].native_value == "0"

    def test_no_packs(self):
        assert _sensors([]) == {}
        coordinator = SimpleNamespace(data={"INV1": {}})
        assert not [
            d
            for d in _build_dynamic_descriptions(coordinator, "INV1")
            if d.key.startswith("battery_pack")
        ]


class TestBatteryWarning:
    """The inverter's own battery warning, from its settings (#48)."""

    @staticmethod
    def _sensor(settings):
        coordinator = SimpleNamespace(data={"INV1": {"settings": settings}})
        return next(
            (
                SunsynkSensor(coordinator, "INV1", desc, MagicMock())
                for desc in _build_dynamic_descriptions(coordinator, "INV1")
                if desc.key == "battery_warning"
            ),
            None,
        )

    @pytest.mark.parametrize(
        ("reported", "shown"), [("64", "64"), (64, "64"), ("0", "0"), (0, "0")]
    )
    def test_the_code_as_reported(self, reported, shown):
        sensor = self._sensor({"batWarn": reported})
        assert sensor.native_value == shown
        assert sensor.entity_description.name == "Battery Warning"
        assert sensor.entity_description.state_class is None
        assert sensor.entity_description.entity_category == EntityCategory.DIAGNOSTIC

    @pytest.mark.parametrize("settings", [{}, {"batWarn": None}, {"batWarn": ""}])
    def test_no_sensor_when_not_reported(self, settings):
        assert self._sensor(settings) is None


def test_pack_serials_are_redacted_in_diagnostics():
    for key in ("sn", "gsn", "id", "invSn", "packSN", "packSn", "bmsSn", "parentSn"):
        assert key in _TO_REDACT

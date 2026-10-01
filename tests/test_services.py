"""Tests for unambiguous per-inverter service routing."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.sunsynk import (
    _dashboard_enabled,
    _find_virtual_slot_scheduler,
    _migrate_virtual_slot_entity_unique_ids,
    async_setup,
    async_unload_entry,
)
from custom_components.sunsynk.const import DOMAIN
from custom_components.sunsynk.coordinator import SunsynkCoordinator


def _coordinator() -> SunsynkCoordinator:
    coordinator = object.__new__(SunsynkCoordinator)
    coordinator.serials = ["SLAVE1", "MASTER1", "INV2"]
    coordinator.data = {
        "SLAVE1": {"inverter": {"parallel": 1, "equipMode": 0}},
        "MASTER1": {"inverter": {"parallel": 1, "equipMode": 1}},
        "INV2": {"inverter": {}},
    }
    return coordinator


def test_dashboard_is_opt_in_and_options_override_entry_data():
    assert not _dashboard_enabled(SimpleNamespace(data={}, options={}))
    assert _dashboard_enabled(
        SimpleNamespace(data={"create_dashboard": True}, options={})
    )
    assert not _dashboard_enabled(
        SimpleNamespace(
            data={"create_dashboard": True},
            options={"create_dashboard": False},
        )
    )


@pytest.mark.asyncio
async def test_global_setup_does_not_mutate_lovelace_without_entry_opt_in():
    hass = MagicMock()
    hass.http.async_register_static_paths = AsyncMock()
    hass.services.async_register = MagicMock()

    with patch(
        "custom_components.sunsynk._async_register_lovelace_resource",
        new=AsyncMock(),
    ) as register_resource:
        assert await async_setup(hass, {}) is True

    register_resource.assert_not_awaited()


@pytest.mark.asyncio
async def test_registered_services_route_and_execute_on_minimal_ha():
    coordinator = _coordinator()
    coordinator.async_write_setting = AsyncMock()
    scheduler = MagicMock(async_set_slot=AsyncMock(), async_clear_slot=AsyncMock())
    handlers = {}
    hass = MagicMock()
    hass.data = {
        DOMAIN: {
            "entry": coordinator,
            "entry_vslots": {"MASTER1": scheduler, "INV2": MagicMock()},
        }
    }
    hass.http.async_register_static_paths = AsyncMock()
    hass.services.async_register.side_effect = (
        lambda domain, service, handler, schema: handlers.setdefault(service, handler)
    )

    await async_setup(hass, {})

    await handlers["force_charge"](
        SimpleNamespace(data={"serial": "SLAVE1", "current": 20})
    )
    await handlers["force_discharge"](
        SimpleNamespace(data={"serial": "INV2", "current": 30})
    )
    await handlers["set_work_mode"](
        SimpleNamespace(data={"serial": "INV2", "mode": 4})
    )
    await handlers["set_virtual_slot"](
        SimpleNamespace(
            data={
                "serial": "SLAVE1",
                "slot_id": 1,
                "start": "22:00",
                "end": "06:00",
                "mode": "charge",
                "weekdays": ["mon", "sun"],
                "current": 20,
                "target_soc": 90,
                "sell_power": 0,
                "priority": 5,
                "enabled": True,
            }
        )
    )
    await handlers["clear_virtual_slot"](
        SimpleNamespace(data={"serial": "SLAVE1", "slot_id": 1})
    )

    coordinator.async_write_setting.assert_any_await(
        "MASTER1", "chargeCurrent", 20
    )
    coordinator.async_write_setting.assert_any_await(
        "INV2", "dischargeCurrent", 30
    )
    coordinator.async_write_setting.assert_any_await("INV2", "sysWorkMode", 4)
    scheduler.async_set_slot.assert_awaited_once()
    scheduler.async_clear_slot.assert_awaited_once_with(1)

    for service in ("force_charge", "force_discharge", "set_work_mode", "set_virtual_slot", "clear_virtual_slot"):
        with pytest.raises(ValueError, match="No Sunsynk inverter"):
            data = {"serial": "UNKNOWN", "current": 1, "mode": 1, "slot_id": 1}
            await handlers[service](SimpleNamespace(data=data))


def test_virtual_slot_service_routes_parallel_slave_to_master_scheduler():
    coordinator = _coordinator()
    master_scheduler = MagicMock()
    independent_scheduler = MagicMock()
    hass = SimpleNamespace(
        data={
            DOMAIN: {
                "entry": coordinator,
                "entry_vslots": {
                    "MASTER1": master_scheduler,
                    "INV2": independent_scheduler,
                },
            }
        }
    )

    assert _find_virtual_slot_scheduler(hass, "SLAVE1") is master_scheduler
    assert _find_virtual_slot_scheduler(hass, "MASTER1") is master_scheduler
    assert _find_virtual_slot_scheduler(hass, "INV2") is independent_scheduler


def test_virtual_slot_service_rejects_unknown_serial():
    coordinator = _coordinator()
    hass = SimpleNamespace(data={DOMAIN: {"entry": coordinator, "entry_vslots": {}}})

    assert _find_virtual_slot_scheduler(hass, "UNKNOWN") is None


def test_virtual_slot_entity_migration_preserves_existing_entity_ids():
    registry = MagicMock()
    registry.async_get_entity_id.side_effect = lambda platform, domain, unique_id: {
        "entry_vslots_enabled": "switch.virtual_slot_scheduler",
        "entry_vslots_state": "sensor.virtual_slot_scheduler",
    }.get(unique_id)

    with patch(
        "custom_components.sunsynk.er.async_get", return_value=registry
    ):
        _migrate_virtual_slot_entity_unique_ids(
            MagicMock(), "entry", "MASTER1"
        )

    assert registry.async_update_entity.call_count == 2
    registry.async_update_entity.assert_any_call(
        "switch.virtual_slot_scheduler",
        new_unique_id="MASTER1_vslots_enabled",
    )
    registry.async_update_entity.assert_any_call(
        "sensor.virtual_slot_scheduler",
        new_unique_id="MASTER1_vslots_state",
    )


def _record(events: list[str], name: str):
    def _side_effect(*args, **kwargs):
        events.append(name)

    return _side_effect


@pytest.mark.asyncio
async def test_unload_restores_before_closing_coordinator():
    events: list[str] = []
    entry = MagicMock(entry_id="entry")
    coordinator = MagicMock(
        async_close=AsyncMock(side_effect=_record(events, "coordinator_close"))
    )
    tariff = MagicMock(
        stop=MagicMock(side_effect=_record(events, "tariff_stop")),
        async_shutdown=AsyncMock(side_effect=_record(events, "tariff_restore")),
    )
    scheduler = MagicMock(
        stop=MagicMock(side_effect=_record(events, "scheduler_stop")),
        async_shutdown=AsyncMock(side_effect=_record(events, "scheduler_restore")),
    )
    config_entries = MagicMock(
        async_unload_platforms=AsyncMock(
            side_effect=lambda *args: events.append("platform_unload") or True
        )
    )
    hass = SimpleNamespace(
        config_entries=config_entries,
        data={
            DOMAIN: {
                "entry": coordinator,
                "entry_tariff": tariff,
                "entry_vslots": {"INV1": scheduler},
            }
        },
    )

    assert await async_unload_entry(hass, entry) is True

    assert events == [
        "tariff_stop",
        "scheduler_stop",
        "platform_unload",
        "tariff_restore",
        "scheduler_restore",
        "coordinator_close",
    ]
    assert hass.data[DOMAIN] == {}


@pytest.mark.asyncio
async def test_failed_platform_unload_restarts_listeners_without_restoring():
    entry = MagicMock(entry_id="entry")
    coordinator = MagicMock(async_close=AsyncMock())
    tariff = MagicMock(async_shutdown=AsyncMock())
    scheduler = MagicMock(async_shutdown=AsyncMock())
    hass = SimpleNamespace(
        config_entries=MagicMock(
            async_unload_platforms=AsyncMock(return_value=False)
        ),
        data={
            DOMAIN: {
                "entry": coordinator,
                "entry_tariff": tariff,
                "entry_vslots": {"INV1": scheduler},
            }
        },
    )

    assert await async_unload_entry(hass, entry) is False

    tariff.stop.assert_called_once()
    tariff.start.assert_called_once()
    scheduler.stop.assert_called_once()
    scheduler.start.assert_called_once()
    tariff.async_shutdown.assert_not_awaited()
    scheduler.async_shutdown.assert_not_awaited()
    coordinator.async_close.assert_not_awaited()

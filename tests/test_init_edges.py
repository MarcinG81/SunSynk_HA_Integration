"""Tests for integration wiring, dashboard and compatibility fallbacks."""
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import custom_components.sunsynk as integration
from custom_components.sunsynk.const import DOMAIN


@pytest.mark.asyncio
async def test_maybe_await_handles_plain_and_awaitable_values():
    assert await integration._maybe_await(3) == 3
    assert await integration._maybe_await(asyncio.sleep(0, result=4)) == 4


def test_manifest_version_falls_back_on_read_error():
    with patch.object(Path, "open", side_effect=OSError("boom")):
        assert integration._read_manifest_version() == "0"


@pytest.mark.asyncio
async def test_lovelace_resource_unavailable():
    hass = SimpleNamespace(data={})
    await integration._async_register_lovelace_resource(hass)


@pytest.mark.asyncio
async def test_lovelace_resource_updates_existing_item():
    resources = MagicMock(loaded=False)
    resources.async_load = AsyncMock()
    resources.async_items = MagicMock(
        return_value=[{"id": "1", "url": "/sunsynk/sunsynk-power-flow-card.js", "type": "css"}]
    )
    resources.async_update_item = AsyncMock()
    hass = SimpleNamespace(data={"lovelace": SimpleNamespace(resources=resources)})
    await integration._async_register_lovelace_resource(hass)
    resources.async_load.assert_awaited_once()
    resources.async_update_item.assert_awaited_once()


@pytest.mark.asyncio
async def test_lovelace_resource_create_read_only_and_error_paths():
    create = MagicMock(
        async_items=AsyncMock(return_value=[]), async_create_item=AsyncMock()
    )
    await integration._async_register_lovelace_resource(
        SimpleNamespace(data={"lovelace": SimpleNamespace(resources=create)})
    )
    create.async_create_item.assert_awaited_once()

    read_only = SimpleNamespace(async_items=lambda: [])
    await integration._async_register_lovelace_resource(
        SimpleNamespace(data={"lovelace": SimpleNamespace(resources=read_only)})
    )

    broken = SimpleNamespace(async_items=MagicMock(side_effect=RuntimeError("boom")))
    await integration._async_register_lovelace_resource(
        SimpleNamespace(data={"lovelace": SimpleNamespace(resources=broken)})
    )


@pytest.mark.asyncio
async def test_setup_static_path_failure_still_registers_services():
    hass = MagicMock()
    hass.http.async_register_static_paths = AsyncMock(side_effect=AttributeError)
    hass.http.register_static_path.side_effect = RuntimeError("boom")
    assert await integration.async_setup(hass, {}) is True
    registered = {
        call.args[1] for call in hass.services.async_register.call_args_list
    }
    assert registered == {
        integration.SERVICE_FORCE_CHARGE,
        integration.SERVICE_FORCE_DISCHARGE,
        integration.SERVICE_SET_WORK_MODE,
        integration.SERVICE_SET_VIRTUAL_SLOT,
        integration.SERVICE_CLEAR_VIRTUAL_SLOT,
    }


@pytest.mark.asyncio
async def test_setup_static_path_legacy_api_success():
    hass = MagicMock()
    hass.http.async_register_static_paths = AsyncMock(side_effect=AttributeError)
    assert await integration.async_setup(hass, {}) is True
    hass.http.register_static_path.assert_called_once()
    assert hass.services.async_register.call_count == 5


@pytest.mark.asyncio
async def test_service_handlers_reject_missing_scheduler():
    handlers = {}
    hass = MagicMock()
    hass.data = {DOMAIN: {}}
    hass.http.async_register_static_paths = AsyncMock()
    hass.services.async_register.side_effect = (
        lambda domain, service, handler, schema: handlers.__setitem__(service, handler)
    )
    await integration.async_setup(hass, {})
    coordinator = MagicMock(spec=integration.SunsynkCoordinator)
    coordinator.serials = ["SN1"]
    coordinator.resolve_write_target.return_value = "SN1"
    hass.data[DOMAIN]["entry"] = coordinator

    for service, data in (
        (
            integration.SERVICE_SET_VIRTUAL_SLOT,
            {
                "serial": "SN1",
                "slot_id": 1,
                "start": "00:00",
                "end": "01:00",
                "mode": "idle",
                "sell_power": 0,
                "priority": 0,
                "enabled": True,
            },
        ),
        (integration.SERVICE_CLEAR_VIRTUAL_SLOT, {"serial": "SN1", "slot_id": 1}),
    ):
        with pytest.raises(ValueError, match="not initialised"):
            await handlers[service](SimpleNamespace(data=data))


@pytest.mark.asyncio
async def test_dashboard_frontend_success_and_failure(mock_hass):
    with (
        patch(
            "homeassistant.components.frontend.add_extra_js_url"
        ) as add_extra,
        patch.object(
            integration, "_async_register_lovelace_resource", new=AsyncMock()
        ) as register,
    ):
        await integration._async_enable_dashboard_frontend(mock_hass)
    add_extra.assert_called_once()
    register.assert_awaited_once()

    with (
        patch(
            "homeassistant.components.frontend.add_extra_js_url",
            side_effect=RuntimeError("boom"),
        ),
        patch.object(
            integration, "_async_register_lovelace_resource", new=AsyncMock()
        ) as register,
    ):
        await integration._async_enable_dashboard_frontend(mock_hass)
    register.assert_awaited_once()


def _dashboard_context(dashboards):
    entity = SimpleNamespace(
        platform=DOMAIN,
        unique_id="SN1_battery_soc",
        entity_id="sensor.battery_soc",
    )
    registry = SimpleNamespace(entities={"sensor.battery_soc": entity})
    hass = SimpleNamespace(data={"lovelace": SimpleNamespace(dashboards=dashboards)})
    entry = MagicMock(entry_id="ABCDEF1234")
    coordinator = MagicMock(
        serials=["SN1"],
        data={"SN1": {"inverter": {"alias": "My Solar"}}},
    )
    coordinator.resolve_write_target.return_value = "SN1"
    return hass, entry, coordinator, registry


@pytest.mark.asyncio
async def test_dashboard_updates_existing_and_handles_save_error():
    for side_effect in (None, RuntimeError("boom")):
        dashboard = MagicMock(async_save=AsyncMock(side_effect=side_effect))
        dashboards = {"sunsynk-abcdef12": dashboard}
        hass, entry, coordinator, registry = _dashboard_context(dashboards)
        with (
            patch("custom_components.sunsynk.er.async_get", return_value=registry),
            patch("custom_components.sunsynk.build_dashboard", return_value={}),
        ):
            await integration._async_setup_dashboard(hass, entry, coordinator)
        dashboard.async_save.assert_awaited_once()


@pytest.mark.asyncio
async def test_dashboard_builder_resolves_registered_entity_id():
    dashboard = MagicMock(async_save=AsyncMock())
    hass, entry, coordinator, registry = _dashboard_context(
        {"sunsynk-abcdef12": dashboard}
    )

    def _build(_prefix, entity_id, *_args):
        assert entity_id("battery_soc") == "sensor.battery_soc"
        return {}

    with (
        patch("custom_components.sunsynk.er.async_get", return_value=registry),
        patch("custom_components.sunsynk.build_dashboard", side_effect=_build),
    ):
        await integration._async_setup_dashboard(hass, entry, coordinator)


class _DashboardCollection(dict):
    def __init__(self, fail_create=False, fail_save=False):
        super().__init__()
        self.fail_create = fail_create
        self.fail_save = fail_save

    async def async_create_item(self, item):
        if self.fail_create:
            raise RuntimeError("create failed")
        self[item["url_path"]] = MagicMock(
            async_save=AsyncMock(
                side_effect=RuntimeError("save failed") if self.fail_save else None
            )
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(("fail_create", "fail_save"), [(False, False), (True, False), (False, True)])
async def test_dashboard_register_paths(fail_create, fail_save):
    dashboards = _DashboardCollection(fail_create, fail_save)
    hass, entry, coordinator, registry = _dashboard_context(dashboards)
    with (
        patch("custom_components.sunsynk.er.async_get", return_value=registry),
        patch("custom_components.sunsynk.build_dashboard", return_value={}),
    ):
        await integration._async_setup_dashboard(hass, entry, coordinator)


@pytest.mark.asyncio
async def test_dashboard_storage_fallback_all_branches():
    hass, entry, coordinator, registry = _dashboard_context(None)
    content = MagicMock(async_save=AsyncMock())
    index = MagicMock(
        async_load=AsyncMock(return_value={"items": "invalid"}),
        async_save=AsyncMock(),
    )
    with (
        patch("custom_components.sunsynk.er.async_get", return_value=registry),
        patch("custom_components.sunsynk.build_dashboard", return_value={}),
        patch("homeassistant.helpers.storage.Store", side_effect=[content, index]),
    ):
        await integration._async_setup_dashboard(hass, entry, coordinator)
    content.async_save.assert_awaited_once()
    index.async_save.assert_awaited_once()

    store_pairs = (
        (
            MagicMock(async_save=AsyncMock(side_effect=RuntimeError("content"))),
            MagicMock(async_save=AsyncMock(), async_load=AsyncMock(return_value={})),
        ),
        (
            MagicMock(async_save=AsyncMock()),
            MagicMock(
                async_save=AsyncMock(),
                async_load=AsyncMock(side_effect=RuntimeError("index")),
            ),
        ),
    )
    for first_store, second_store in store_pairs:
        with (
            patch("custom_components.sunsynk.er.async_get", return_value=registry),
            patch("custom_components.sunsynk.build_dashboard", return_value={}),
            patch(
                "homeassistant.helpers.storage.Store",
                side_effect=[first_store, second_store],
            ),
        ):
            await integration._async_setup_dashboard(hass, entry, coordinator)


@pytest.mark.asyncio
async def test_unload_continues_after_restore_errors(mock_hass):
    entry = MagicMock(entry_id="entry")
    coordinator = MagicMock(async_close=AsyncMock())
    tariff = MagicMock(
        stop=MagicMock(), async_shutdown=AsyncMock(side_effect=RuntimeError("tariff"))
    )
    scheduler = MagicMock(
        stop=MagicMock(), async_shutdown=AsyncMock(side_effect=RuntimeError("slot"))
    )
    mock_hass.data = {
        DOMAIN: {
            "entry": coordinator,
            "entry_tariff": tariff,
            "entry_vslots": {"SN1": scheduler},
        }
    }
    mock_hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
    assert await integration.async_unload_entry(mock_hass, entry) is True
    coordinator.async_close.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_listener_reloads_entry(mock_hass):
    mock_hass.config_entries.async_reload = AsyncMock()
    await integration._async_update_listener(
        mock_hass, MagicMock(entry_id="entry")
    )
    mock_hass.config_entries.async_reload.assert_awaited_once_with("entry")

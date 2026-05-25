"""Sunsynk / Deye Solar Inverter integration for Home Assistant."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import voluptuous as vol
import homeassistant.helpers.config_validation as cv
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, ServiceCall

from .api.auth import SunsynkAuth
from .const import (
    CONF_API_SERVER,
    CONF_CHEAP_CHARGE_CURRENT,
    CONF_CHEAP_TARGET_SOC,
    CONF_CHEAP_THRESHOLD,
    CONF_DISCHARGE_MIN_SOC,
    CONF_EXPENSIVE_THRESHOLD,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_NORMAL_CHARGE_CURRENT,
    CONF_NORMAL_DISCHARGE_CURRENT,
    CONF_PANEL_KWP,
    CONF_PEAK_DISCHARGE_CURRENT,
    CONF_PERFORMANCE_RATIO,
    CONF_PRICE_ENTITY,
    CONF_REFRESH_INTERVAL,
    CONF_SERIALS,
    CONF_TARIFF_END_HOUR,
    CONF_TARIFF_START_HOUR,
    CONF_PRICE_MAX_AGE,
    DEFAULT_CHEAP_TARGET_SOC,
    DEFAULT_PRICE_MAX_AGE,
    DEFAULT_DISCHARGE_MIN_SOC,
    DEFAULT_PERFORMANCE_RATIO,
    DEFAULT_REFRESH_INTERVAL,
    DOMAIN,
)
from .coordinator import SolarForecastCoordinator, SunsynkCoordinator
from .dashboard import build_dashboard
from .tariff import TariffChargingManager

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.NUMBER, Platform.SWITCH, Platform.TEXT]

SERVICE_FORCE_CHARGE = "force_charge"
SERVICE_FORCE_DISCHARGE = "force_discharge"
SERVICE_SET_WORK_MODE = "set_work_mode"

_SERVICE_SERIAL_CURRENT_SCHEMA = vol.Schema({
    vol.Required("serial"): str,
    vol.Required("current"): vol.All(vol.Coerce(int), vol.Range(min=0, max=500)),
})
_SERVICE_SET_WORK_MODE_SCHEMA = vol.Schema({
    vol.Required("serial"): str,
    vol.Required("mode"): vol.All(vol.Coerce(int), vol.Range(min=0, max=4)),
})

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

_CARD_JS = "sunsynk-power-flow-card.js"
_CARD_URL = f"/sunsynk/{_CARD_JS}"


def _find_coordinator(hass: HomeAssistant, serial: str) -> SunsynkCoordinator | None:
    """Find the coordinator that owns a given inverter serial."""
    for val in hass.data.get(DOMAIN, {}).values():
        if isinstance(val, SunsynkCoordinator) and serial in val.serials:
            return val
    return None


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register the bundled Sunsynk Power Flow Card frontend resource."""
    card_path = str(Path(__file__).parent / "www" / _CARD_JS)

    # Register static path — API changed in HA 2024.7
    try:
        from homeassistant.components.http import StaticPathConfig  # HA 2024.7+
        await hass.http.async_register_static_paths(
            [StaticPathConfig(_CARD_URL, card_path, False)]
        )
    except (ImportError, AttributeError):
        try:
            hass.http.register_static_path(_CARD_URL, card_path, cache_headers=False)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Could not serve card static file: %s", err)
            return True

    # Register as extra frontend module so HA loads it automatically
    try:
        from homeassistant.components.frontend import add_extra_js_url
        add_extra_js_url(hass, _CARD_URL)
    except Exception:  # noqa: BLE001
        pass

    _LOGGER.debug("Sunsynk Power Flow Card registered at %s", _CARD_URL)

    async def _handle_force_charge(call: ServiceCall) -> None:
        serial: str = call.data["serial"]
        coordinator = _find_coordinator(hass, serial)
        if coordinator is None:
            raise ValueError(f"No Sunsynk inverter found with serial {serial!r}")
        await coordinator.async_write_setting(serial, "chargeCurrent", call.data["current"])

    async def _handle_force_discharge(call: ServiceCall) -> None:
        serial: str = call.data["serial"]
        coordinator = _find_coordinator(hass, serial)
        if coordinator is None:
            raise ValueError(f"No Sunsynk inverter found with serial {serial!r}")
        await coordinator.async_write_setting(serial, "dischargeCurrent", call.data["current"])

    async def _handle_set_work_mode(call: ServiceCall) -> None:
        serial: str = call.data["serial"]
        coordinator = _find_coordinator(hass, serial)
        if coordinator is None:
            raise ValueError(f"No Sunsynk inverter found with serial {serial!r}")
        await coordinator.async_write_setting(serial, "sysWorkMode", call.data["mode"])

    hass.services.async_register(
        DOMAIN, SERVICE_FORCE_CHARGE, _handle_force_charge, _SERVICE_SERIAL_CURRENT_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_FORCE_DISCHARGE, _handle_force_discharge, _SERVICE_SERIAL_CURRENT_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SET_WORK_MODE, _handle_set_work_mode, _SERVICE_SET_WORK_MODE_SCHEMA
    )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Sunsynk from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    refresh_interval = entry.options.get(
        CONF_REFRESH_INTERVAL,
        entry.data.get(CONF_REFRESH_INTERVAL, DEFAULT_REFRESH_INTERVAL),
    )
    serials: list[str] = entry.options.get(
        CONF_SERIALS, entry.data.get(CONF_SERIALS, [])
    )

    auth = SunsynkAuth(
        api_server=entry.data[CONF_API_SERVER],
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
    )

    coordinator = SunsynkCoordinator(
        hass,
        auth=auth,
        serials=serials,
        refresh_interval=refresh_interval,
        entry_id=entry.entry_id,
    )

    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    kwp = entry.options.get(CONF_PANEL_KWP, entry.data.get(CONF_PANEL_KWP))
    if kwp is not None:
        lat = entry.options.get(CONF_LATITUDE, entry.data.get(CONF_LATITUDE, hass.config.latitude))
        lon = entry.options.get(CONF_LONGITUDE, entry.data.get(CONF_LONGITUDE, hass.config.longitude))
        pr = entry.options.get(
            CONF_PERFORMANCE_RATIO,
            entry.data.get(CONF_PERFORMANCE_RATIO, DEFAULT_PERFORMANCE_RATIO),
        )
        forecast_coordinator = SolarForecastCoordinator(
            hass,
            latitude=float(lat),
            longitude=float(lon),
            panel_kwp=float(kwp),
            performance_ratio=float(pr),
        )
        try:
            await forecast_coordinator.async_config_entry_first_refresh()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Solar forecast initial fetch failed (will retry): %s", err)
        hass.data[DOMAIN][f"{entry.entry_id}_forecast"] = forecast_coordinator

    # Tariff manager must be created before platform setup so number.py can find it
    price_entity = entry.options.get(CONF_PRICE_ENTITY, entry.data.get(CONF_PRICE_ENTITY))
    if price_entity:
        def _opt(key: str, default: Any = None) -> Any:
            return entry.options.get(key, entry.data.get(key, default))

        tariff_manager = TariffChargingManager(
            hass=hass,
            coordinator=coordinator,
            price_entity=price_entity,
            cheap_threshold=_opt(CONF_CHEAP_THRESHOLD),
            cheap_current=_opt(CONF_CHEAP_CHARGE_CURRENT),
            normal_charge_current=_opt(CONF_NORMAL_CHARGE_CURRENT),
            target_soc=_opt(CONF_CHEAP_TARGET_SOC, DEFAULT_CHEAP_TARGET_SOC),
            expensive_threshold=_opt(CONF_EXPENSIVE_THRESHOLD),
            peak_discharge_current=_opt(CONF_PEAK_DISCHARGE_CURRENT),
            normal_discharge_current=_opt(CONF_NORMAL_DISCHARGE_CURRENT),
            discharge_min_soc=_opt(CONF_DISCHARGE_MIN_SOC, DEFAULT_DISCHARGE_MIN_SOC),
            start_hour=_opt(CONF_TARIFF_START_HOUR),
            end_hour=_opt(CONF_TARIFF_END_HOUR),
            price_max_age_minutes=_opt(CONF_PRICE_MAX_AGE, DEFAULT_PRICE_MAX_AGE),
        )
        hass.data[DOMAIN][f"{entry.entry_id}_tariff"] = tariff_manager

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Start tariff manager after platforms are set up (switch/sensor already registered)
    tariff_manager = hass.data[DOMAIN].get(f"{entry.entry_id}_tariff")
    if tariff_manager is not None:
        tariff_manager.start()

    hass.async_create_task(_async_setup_dashboard(hass, entry, coordinator))

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        coordinator: SunsynkCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_close()
        forecast_coordinator: SolarForecastCoordinator | None = hass.data[DOMAIN].pop(
            f"{entry.entry_id}_forecast", None
        )
        if forecast_coordinator is not None:
            await forecast_coordinator.async_close()
        tariff_manager: TariffChargingManager | None = hass.data[DOMAIN].pop(
            f"{entry.entry_id}_tariff", None
        )
        if tariff_manager is not None:
            tariff_manager.stop()

    return unload_ok


async def _async_setup_dashboard(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: SunsynkCoordinator,
) -> None:
    """Auto-create a Lovelace dashboard with correct entity IDs for this inverter."""
    first_serial = coordinator.serials[0] if coordinator.serials else ""
    inverter_data = (coordinator.data or {}).get(first_serial, {}).get("inverter", {})
    alias = inverter_data.get("alias") or f"Sunsynk {first_serial}"
    prefix = re.sub(r"[^a-z0-9]+", "_", alias.lower()).strip("_")
    url_path = f"sunsynk-{entry.entry_id[:8].lower()}"

    # Look up actual entity IDs from the registry (unique_id = "{serial}_{key}")
    from homeassistant.helpers import entity_registry as er
    reg = er.async_get(hass)
    uid_map: dict[str, str] = {
        e.unique_id[len(first_serial) + 1:]: e.entity_id
        for e in reg.entities.values()
        if e.platform == DOMAIN and e.unique_id.startswith(f"{first_serial}_")
    }
    def eid(key: str) -> str | None:
        return uid_map.get(key)

    forecast_prefix = f"{entry.entry_id}_forecast_"
    forecast_uid_map: dict[str, str] = {
        e.unique_id[len(forecast_prefix):]: e.entity_id
        for e in reg.entities.values()
        if e.platform == DOMAIN and e.unique_id.startswith(forecast_prefix)
    }
    forecast_eid_fn = (lambda key: forecast_uid_map.get(key)) if forecast_uid_map else None

    tariff_prefix = f"{entry.entry_id}_tariff_"
    tariff_uid_map: dict[str, str] = {
        e.unique_id[len(tariff_prefix):]: e.entity_id
        for e in reg.entities.values()
        if e.platform == DOMAIN and e.unique_id.startswith(tariff_prefix)
    }
    tariff_eid_fn = (lambda key: tariff_uid_map.get(key)) if tariff_uid_map else None

    dashboard_config = build_dashboard(prefix, eid, forecast_eid_fn, tariff_eid_fn, entry.entry_id)

    lovelace = hass.data.get("lovelace")
    dashboards = getattr(lovelace, "dashboards", None)

    # ── Path A: dashboard already registered in lovelace (in-memory dict) ──
    # dashboards is a dict: url_path → LovelaceStorage object.
    # Save our config ON that object so it updates both its cache and the file.
    if isinstance(dashboards, dict) and url_path in dashboards:
        ha_config = dashboards[url_path]
        if hasattr(ha_config, "async_save"):
            try:
                await ha_config.async_save(dashboard_config)
            except Exception as err:  # noqa: BLE001
                _LOGGER.error("Sunsynk: dashboard save failed: %s", err)
        return

    # ── Path B: dashboard NOT registered yet ────────────────────────────
    # Register in sidebar FIRST, then save content into the registered object.
    # async_create_item creates its own LovelaceStorage internally — if we save
    # content before registering, that content gets overwritten by the empty init.
    _item = {
        "url_path": url_path,
        "require_admin": False,
        "mode": "storage",
        "title": f"Solar {alias}",
        "icon": "mdi:solar-power-variant",
        "show_in_sidebar": True,
    }
    if dashboards is not None and hasattr(dashboards, "async_create_item"):
        try:
            await dashboards.async_create_item(_item)
            _LOGGER.info("Sunsynk: dashboard registered at /%s", url_path)
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Sunsynk: dashboard registration failed: %s", err)

        # Save content into the freshly registered dashboard object
        if isinstance(dashboards, dict) and url_path in dashboards:
            try:
                await dashboards[url_path].async_save(dashboard_config)
                _LOGGER.info("Sunsynk: dashboard content saved")
            except Exception as err:  # noqa: BLE001
                _LOGGER.error("Sunsynk: dashboard content save failed: %s", err)
    else:
        # Fallback: write content and registration directly to storage files.
        # Only takes effect after a full HA restart.
        try:
            from homeassistant.helpers.storage import Store
            await Store(hass, 1, f"lovelace.{url_path}").async_save({"config": dashboard_config})
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Sunsynk: dashboard content store failed: %s", err)
        try:
            import uuid
            from homeassistant.helpers.storage import Store
            ds = Store(hass, 1, "lovelace_dashboards")
            data = await ds.async_load() or {}
            items: list = data.get("items") or []
            if not isinstance(items, list):
                items = []
            if not any(isinstance(v, dict) and v.get("url_path") == url_path for v in items):
                items.append({"id": uuid.uuid4().hex, **_item})
                data["items"] = items
                await ds.async_save(data)
            _LOGGER.warning("Sunsynk: dashboard saved to storage — restart HA to see it in sidebar")
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Sunsynk: lovelace_dashboards write failed: %s", err)


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)

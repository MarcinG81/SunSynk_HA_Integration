"""Sunsynk / Deye Solar Inverter integration for Home Assistant."""

from __future__ import annotations

import inspect
import json
import logging
import re
from pathlib import Path
from typing import Any

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.auth.permissions.const import POLICY_CONTROL
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import (
    ConfigEntryError,
    ServiceValidationError,
    Unauthorized,
    UnknownUser,
)
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store

from .api.auth import SunsynkAuth
from .backup import async_backup, async_restore
from .calibration import PerformanceRatioCalibrator
from .const import (
    CONF_ADMIN_ONLY_CONTROL,
    CONF_API_SERVER,
    CONF_CHEAP_CHARGE_CURRENT,
    CONF_CHEAP_TARGET_SOC,
    CONF_CHEAP_THRESHOLD,
    CONF_CREATE_DASHBOARD,
    CONF_DEYE_APP_ID,
    CONF_DEYE_APP_SECRET,
    CONF_DEYE_COMPANY_ID,
    CONF_DEYE_LOGIN,
    CONF_DEYE_PASSWORD_HASH,
    CONF_DEYE_REGION,
    CONF_DISCHARGE_MIN_SOC,
    CONF_EXPENSIVE_THRESHOLD,
    CONF_EXPORT_PRICE_ENTITY,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_MAX_EXPORT_POWER,
    CONF_NEGATIVE_PRICE_PROTECTION,
    CONF_NORMAL_CHARGE_CURRENT,
    CONF_NORMAL_DISCHARGE_CURRENT,
    CONF_PANEL_KWP,
    CONF_PEAK_DISCHARGE_CURRENT,
    CONF_PERFORMANCE_RATIO,
    CONF_PRICE_ENTITY,
    CONF_PRICE_MAX_AGE,
    CONF_PRICE_STALE_CHECK,
    CONF_REFRESH_INTERVAL,
    CONF_SERIALS,
    CONF_TARIFF_ENABLED,
    CONF_TARIFF_END_HOUR,
    CONF_TARIFF_START_HOUR,
    CONF_TARIFF_TEST_MODE,
    DEFAULT_CHEAP_TARGET_SOC,
    DEFAULT_DISCHARGE_MIN_SOC,
    DEFAULT_PERFORMANCE_RATIO,
    DEFAULT_PRICE_MAX_AGE,
    DEFAULT_REFRESH_INTERVAL,
    DOMAIN,
    is_deyecloud,
)
from .coordinator import SolarForecastCoordinator, SunsynkCoordinator
from .current_limits import current_limit, describe_limit, group_sizes
from .dashboard import build_dashboard
from .deyecloud.coordinator import DeyeCloudCoordinator
from .helpers import options_snapshot_key
from .tariff import MANUAL_CHARGE, MANUAL_DISCHARGE, TariffChargingManager
from .virtual_slots import (
    MAX_VIRTUAL_SLOTS,
    MODE_CHARGE,
    MODE_DISCHARGE,
    STORAGE_VERSION,
    WEEKDAY_NAMES,
    VirtualSlot,
    VirtualSlotScheduler,
)
from .write_validation import MAX_CURRENT_A, MAX_POWER_W

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.SENSOR,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SWITCH,
    Platform.TEXT,
]

# Deye Cloud entries are read-only for now (writes not tested on a real
# inverter yet), so they get sensors only: no controls, Tariff Manager or
# Virtual Slot Scheduler.
READ_ONLY_PLATFORMS = [Platform.SENSOR]

_DEYECLOUD_KEYS = (
    CONF_DEYE_REGION,
    CONF_DEYE_APP_ID,
    CONF_DEYE_APP_SECRET,
    CONF_DEYE_LOGIN,
    CONF_DEYE_PASSWORD_HASH,
)


def _platforms(entry: ConfigEntry) -> list[Platform]:
    return READ_ONLY_PLATFORMS if is_deyecloud(entry.data) else PLATFORMS


SERVICE_BACKUP_SETTINGS = "backup_settings"
SERVICE_RESTORE_SETTINGS = "restore_settings"
SERVICE_CHARGE_NOW_START = "charge_now_start"
SERVICE_CHARGE_NOW_STOP = "charge_now_stop"
SERVICE_DISCHARGE_NOW_START = "discharge_now_start"
SERVICE_DISCHARGE_NOW_STOP = "discharge_now_stop"
SERVICE_FORCE_CHARGE = "force_charge"
SERVICE_FORCE_DISCHARGE = "force_discharge"
SERVICE_SET_WORK_MODE = "set_work_mode"
SERVICE_SET_VIRTUAL_SLOT = "set_virtual_slot"
SERVICE_CLEAR_VIRTUAL_SLOT = "clear_virtual_slot"

_SERVICE_SERIAL_CURRENT_SCHEMA = vol.Schema(
    {
        vol.Required("serial"): str,
        vol.Required("current"): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=MAX_CURRENT_A)
        ),
    }
)
_SERVICE_NOW_START_SCHEMA = vol.Schema(
    {
        vol.Required("serial"): str,
        # Minutes; without it, until stopped (or, discharging, the minimum SOC).
        vol.Optional("duration"): vol.All(vol.Coerce(int), vol.Range(min=1, max=1440)),
    }
)
_SERVICE_NOW_STOP_SCHEMA = vol.Schema({vol.Required("serial"): str})
_SERVICE_BACKUP_SCHEMA = vol.Schema({vol.Required("serial"): str})
_SERVICE_RESTORE_SCHEMA = vol.Schema(
    {
        vol.Required("serial"): str,
        vol.Required("file"): str,
        # Only list what would change unless told otherwise.
        vol.Optional("dry_run", default=True): bool,
    }
)
_SERVICE_SET_WORK_MODE_SCHEMA = vol.Schema(
    {
        vol.Required("serial"): str,
        # 0 Selling First, 1 Zero Export + Limit to Load Only, 2 Limited to
        # Home: the only modes Sunsynk / Deye inverters have.
        vol.Required("mode"): vol.All(vol.Coerce(int), vol.Range(min=0, max=2)),
    }
)
# Sunsynk's portal UI only ever offers :00/:30 options; 1.9.1 assumed that
# was a hard inverter-side requirement after one reporter's portal test
# rejected 22:45 (#21). A later reporter's real-world use of Predbat —
# which writes 5-minute-granularity schedules straight through this same
# settings-write API — showed their inverter accepts and executes
# non-:00/:30 values without issue (#25), so this only validates a
# well-formed HH:MM now, not a specific minute granularity. If your
# inverter silently ignores a non-:00/:30 value, stick to :00/:30.
_TIME_HH_MM_PATTERN = r"^([01]\d|2[0-3]):([0-5]\d)$"
_SERVICE_SET_VIRTUAL_SLOT_SCHEMA = vol.Schema(
    {
        vol.Required("serial"): str,
        vol.Required("slot_id"): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=MAX_VIRTUAL_SLOTS)
        ),
        vol.Required("start"): cv.matches_regex(_TIME_HH_MM_PATTERN),
        vol.Required("end"): cv.matches_regex(_TIME_HH_MM_PATTERN),
        vol.Optional("weekdays"): [vol.In(WEEKDAY_NAMES)],
        vol.Required("mode"): vol.In(["charge", "discharge", "idle"]),
        vol.Optional("current"): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=MAX_CURRENT_A)
        ),
        vol.Optional("target_soc"): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
        vol.Optional("sell_power", default=0): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=MAX_POWER_W)
        ),
        vol.Optional("priority", default=0): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=100)
        ),
        vol.Optional("enabled", default=True): cv.boolean,
    }
)
_SERVICE_CLEAR_VIRTUAL_SLOT_SCHEMA = vol.Schema(
    {
        vol.Required("serial"): str,
        vol.Required("slot_id"): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=MAX_VIRTUAL_SLOTS)
        ),
    }
)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

_CARD_JS = "sunsynk-power-flow-card.js"
_CARD_URL = f"/sunsynk/{_CARD_JS}"


def _read_manifest_version() -> str:
    """Read the cache-busting card version without making import fragile."""
    fallback = "0"
    try:
        with (Path(__file__).parent / "manifest.json").open(
            encoding="utf-8"
        ) as manifest_file:
            return json.load(manifest_file).get("version", fallback)
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("Could not read manifest.json version: %s", err)
        return fallback


_MANIFEST_VERSION = _read_manifest_version()
_CARD_RESOURCE_URL = f"{_CARD_URL}?v={_MANIFEST_VERSION}"


async def _maybe_await(value: Any) -> Any:
    """Await a value only when Home Assistant exposes it as a coroutine."""
    if inspect.isawaitable(value):
        return await value
    return value


async def _async_register_lovelace_resource(hass: HomeAssistant) -> None:
    """Register the bundled card as a Lovelace module resource."""
    lovelace = hass.data.get("lovelace")
    resources = getattr(lovelace, "resources", None)
    if resources is None:
        _LOGGER.debug(
            "Sunsynk: Lovelace resources unavailable; relying on frontend module load"
        )
        return

    try:
        if (
            hasattr(resources, "async_load")
            and getattr(resources, "loaded", True) is False
        ):
            await resources.async_load()

        items = await _maybe_await(resources.async_items()) or []
        existing = [
            item
            for item in items
            if str(item.get("url", "")).split("?", 1)[0] == _CARD_URL
        ]

        if existing:
            item = existing[0]
            updates: dict[str, str] = {}
            if item.get("url") != _CARD_RESOURCE_URL:
                updates["url"] = _CARD_RESOURCE_URL
            if item.get("type") != "module":
                updates["res_type"] = "module"
            if updates and item.get("id") and hasattr(resources, "async_update_item"):
                await resources.async_update_item(item["id"], updates)
                _LOGGER.debug("Sunsynk Power Flow Card Lovelace resource updated")
            return

        if hasattr(resources, "async_create_item"):
            await resources.async_create_item(
                {
                    "res_type": "module",
                    "url": _CARD_RESOURCE_URL,
                }
            )
            _LOGGER.debug("Sunsynk Power Flow Card Lovelace resource registered")
        else:
            _LOGGER.debug(
                "Sunsynk: Lovelace resources are read-only; add %s manually",
                _CARD_RESOURCE_URL,
            )
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning(
            "Could not register Sunsynk Power Flow Card Lovelace resource: %s",
            err,
        )


def _find_coordinator(hass: HomeAssistant, serial: str) -> SunsynkCoordinator | None:
    """Find the coordinator that owns a given inverter serial."""
    for val in hass.data.get(DOMAIN, {}).values():
        if isinstance(val, SunsynkCoordinator) and serial in val.serials:
            return val
    return None


def _find_entry_id_for_serial(hass: HomeAssistant, serial: str) -> str | None:
    """Find the config entry id that owns a given inverter serial.

    Coordinators are stored at hass.data[DOMAIN][entry_id] (unsuffixed),
    unlike the tariff/vslots/forecast companions which use a suffixed key.
    """
    for key, val in hass.data.get(DOMAIN, {}).items():
        if isinstance(val, SunsynkCoordinator) and serial in val.serials:
            return key
    return None


async def _async_ensure_control_allowed(
    hass: HomeAssistant, call: ServiceCall, serial: str
) -> None:
    """Refuse a control action from a user who may not control this inverter.

    A user must be allowed to control the inverter's entities, so a read-only
    user can't change the inverter through an action (2.0 audit A1). With
    "administrators only" ticked, the user must also be an administrator,
    the same check as Home Assistant's own admin services (suggested by
    David, @davidatjourney). Automations and scripts run without a user and
    are always allowed.
    """
    context = getattr(call, "context", None)
    if context is None or context.user_id is None:
        return
    user = await hass.auth.async_get_user(context.user_id)
    if user is None:
        raise UnknownUser(context=context)

    entry_id = _find_entry_id_for_serial(hass, serial)
    entry = hass.config_entries.async_get_entry(entry_id) if entry_id else None
    if (
        entry is not None
        and entry.options.get(CONF_ADMIN_ONLY_CONTROL) is True
        and not user.is_admin
    ):
        raise Unauthorized(context=context)
    if user.is_admin:
        return

    entity_ids = _entity_ids_for_serial(hass, serial)
    if not any(
        user.permissions.check_entity(entity_id, POLICY_CONTROL)
        for entity_id in entity_ids
    ):
        raise Unauthorized(context=context, permission=POLICY_CONTROL)


def _entity_ids_for_serial(hass: HomeAssistant, serial: str) -> list[str]:
    """The entities of the device for one inverter serial."""
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, serial)})
    if device is None:
        return []
    return [
        entry.entity_id
        for entry in er.async_entries_for_device(er.async_get(hass), device.id)
    ]


def _ensure_current_within_limit(
    coordinator: SunsynkCoordinator, target: str, direction: str, current: int | None
) -> None:
    """Refuse a current the inverter or battery won't use, saying what will."""
    if not current:
        return
    limit = current_limit(
        coordinator.data, [target], direction, group_sizes(coordinator)
    )
    if limit.maximum is not None and current > limit.maximum:
        raise ServiceValidationError(
            f"{current} A is too high: inverter {target} {direction}s the battery "
            f"at no more than {limit.maximum} A ({describe_limit(limit)})"
        )


def _find_virtual_slot_scheduler(
    hass: HomeAssistant, serial: str
) -> VirtualSlotScheduler | None:
    """Find the scheduler for exactly one logical inverter.

    A serial belonging to a parallel slave intentionally selects its master,
    because the master is the physical write target for the entire group.
    """
    entry_id = _find_entry_id_for_serial(hass, serial)
    coordinator = _find_coordinator(hass, serial)
    if entry_id is None or coordinator is None:
        return None
    schedulers: dict[str, VirtualSlotScheduler] = hass.data[DOMAIN].get(
        f"{entry_id}_vslots", {}
    )
    return schedulers.get(coordinator.resolve_write_target(serial))


def _migrate_virtual_slot_entity_unique_ids(
    hass: HomeAssistant, entry_id: str, first_target: str
) -> None:
    """Preserve existing entity IDs while moving to per-inverter unique IDs."""
    registry = er.async_get(hass)
    for platform, suffix in (("switch", "enabled"), ("sensor", "state")):
        old_unique_id = f"{entry_id}_vslots_{suffix}"
        new_unique_id = f"{first_target}_vslots_{suffix}"
        old_entity_id = registry.async_get_entity_id(platform, DOMAIN, old_unique_id)
        new_entity_id = registry.async_get_entity_id(platform, DOMAIN, new_unique_id)
        if old_entity_id is not None and new_entity_id is None:
            registry.async_update_entity(
                old_entity_id,
                new_unique_id=new_unique_id,
            )


# HA suffixes an entity ID that clashes with an existing one: sensor.x_2.
_NUMBERED_ENTITY_ID = re.compile(r"_\d+$")


def _adopt_stray_virtual_slot_entities(
    hass: HomeAssistant,
    coordinator: SunsynkCoordinator,
    targets: set[str],
    entry_id: str,
) -> None:
    """Hand a parallel slave's scheduler entities over to its master's.

    If a parallel group wasn't recognised at setup, the slave got its own
    scheduler and entities, possibly including the entity IDs dashboards and
    automations already use (#21). Keep those IDs on the master's scheduler.

    When both exist, the one to keep is the entity carried over from 1.9.x:
    the registry remembers its previous unique ID. Guessing from HA's "_2"
    clash suffix alone removed the wrong one when the two belonged to
    differently named devices, as on a real parallel system.
    """
    registry = er.async_get(hass)

    def _carried_over(entity_id: str, legacy_unique_id: str) -> bool:
        entry = registry.async_get(entity_id)
        return entry is not None and entry.previous_unique_id == legacy_unique_id

    for serial in coordinator.serials:
        target = coordinator.resolve_write_target(serial)
        if target == serial or target not in targets:
            continue
        for platform, suffix in (("switch", "enabled"), ("sensor", "state")):
            target_unique_id = f"{target}_vslots_{suffix}"
            stray = registry.async_get_entity_id(
                platform, DOMAIN, f"{serial}_vslots_{suffix}"
            )
            if stray is None:
                continue
            owned = registry.async_get_entity_id(platform, DOMAIN, target_unique_id)
            if owned is not None:
                legacy_unique_id = f"{entry_id}_vslots_{suffix}"
                stray_is_original = _carried_over(stray, legacy_unique_id)
                if stray_is_original == _carried_over(owned, legacy_unique_id):
                    stray_is_original = bool(
                        _NUMBERED_ENTITY_ID.search(owned)
                        and not _NUMBERED_ENTITY_ID.search(stray)
                    )
                if stray_is_original:
                    registry.async_remove(owned)
                else:
                    registry.async_remove(stray)
                    continue
            registry.async_update_entity(stray, new_unique_id=target_unique_id)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register the static card path without mutating Lovelace configuration."""
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
            # The card is optional; services must still be registered.
            _LOGGER.warning("Could not serve card static file: %s", err)
        else:
            _LOGGER.debug("Sunsynk Power Flow Card available at %s", _CARD_RESOURCE_URL)
    else:
        _LOGGER.debug("Sunsynk Power Flow Card available at %s", _CARD_RESOURCE_URL)

    async def _handle_force_charge(call: ServiceCall) -> None:
        serial: str = call.data["serial"]
        await _async_ensure_control_allowed(hass, call, serial)
        coordinator = _find_coordinator(hass, serial)
        if coordinator is None:
            raise ValueError(f"No Sunsynk inverter found with serial {serial!r}")
        target = coordinator.resolve_write_target(serial)
        _ensure_current_within_limit(
            coordinator, target, "charge", call.data["current"]
        )
        await coordinator.async_write_setting(
            target, "chargeCurrent", call.data["current"]
        )

    async def _handle_force_discharge(call: ServiceCall) -> None:
        serial: str = call.data["serial"]
        await _async_ensure_control_allowed(hass, call, serial)
        coordinator = _find_coordinator(hass, serial)
        if coordinator is None:
            raise ValueError(f"No Sunsynk inverter found with serial {serial!r}")
        target = coordinator.resolve_write_target(serial)
        _ensure_current_within_limit(
            coordinator, target, "discharge", call.data["current"]
        )
        await coordinator.async_write_setting(
            target, "dischargeCurrent", call.data["current"]
        )

    async def _handle_set_work_mode(call: ServiceCall) -> None:
        serial: str = call.data["serial"]
        await _async_ensure_control_allowed(hass, call, serial)
        coordinator = _find_coordinator(hass, serial)
        if coordinator is None:
            raise ValueError(f"No Sunsynk inverter found with serial {serial!r}")
        target = coordinator.resolve_write_target(serial)
        await coordinator.async_write_setting(target, "sysWorkMode", call.data["mode"])

    async def _handle_set_virtual_slot(call: ServiceCall) -> None:
        serial: str = call.data["serial"]
        await _async_ensure_control_allowed(hass, call, serial)
        coordinator = _find_coordinator(hass, serial)
        if coordinator is None:
            raise ValueError(f"No Sunsynk inverter found with serial {serial!r}")
        scheduler = _find_virtual_slot_scheduler(hass, serial)
        if scheduler is None:
            raise ValueError("Virtual slot scheduler not initialised for this inverter")
        if call.data["mode"] in (MODE_CHARGE, MODE_DISCHARGE):
            _ensure_current_within_limit(
                coordinator,
                scheduler.serial,
                call.data["mode"],
                call.data.get("current"),
            )
        if call.data["mode"] == MODE_DISCHARGE:
            max_sell, why = scheduler.sell_power_limit()
            if max_sell is not None and call.data["sell_power"] > max_sell:
                raise ServiceValidationError(
                    f"Sell power {call.data['sell_power']} W is too high: "
                    f"inverter {scheduler.serial} may sell at most {max_sell} W "
                    f"({why})"
                )
        weekdays = call.data.get("weekdays") or list(WEEKDAY_NAMES)
        slot = VirtualSlot(
            slot_id=call.data["slot_id"],
            start=call.data["start"],
            end=call.data["end"],
            mode=call.data["mode"],
            weekdays=frozenset(WEEKDAY_NAMES.index(d) for d in weekdays),
            current=call.data.get("current"),
            target_soc=call.data.get("target_soc"),
            sell_power=call.data["sell_power"],
            priority=call.data["priority"],
            enabled=call.data["enabled"],
        )
        await scheduler.async_set_slot(slot)

    async def _handle_clear_virtual_slot(call: ServiceCall) -> None:
        serial: str = call.data["serial"]
        await _async_ensure_control_allowed(hass, call, serial)
        if _find_coordinator(hass, serial) is None:
            raise ValueError(f"No Sunsynk inverter found with serial {serial!r}")
        scheduler = _find_virtual_slot_scheduler(hass, serial)
        if scheduler is None:
            raise ValueError("Virtual slot scheduler not initialised for this inverter")
        await scheduler.async_clear_slot(call.data["slot_id"])

    def _manual_target(serial: str) -> tuple[TariffChargingManager, str]:
        coordinator = _find_coordinator(hass, serial)
        if coordinator is None:
            raise ValueError(f"No Sunsynk inverter found with serial {serial!r}")
        entry_id = _find_entry_id_for_serial(hass, serial)
        manager = hass.data[DOMAIN].get(f"{entry_id}_tariff")
        if manager is None:
            raise ServiceValidationError(
                "Tariff Manager isn't set up for this inverter: choose a price "
                "entity and its currents in Configure first. It doesn't have to "
                "be switched on."
            )
        return manager, coordinator.resolve_write_target(serial)

    def _manual_handler(mode: str, start: bool):
        async def _handle(call: ServiceCall) -> None:
            serial: str = call.data["serial"]
            await _async_ensure_control_allowed(hass, call, serial)
            manager, target = _manual_target(serial)
            if start:
                await manager.async_start_manual(
                    target, mode, call.data.get("duration")
                )
            else:
                await manager.async_stop_manual(target, mode)

        return _handle

    def _busy(entry_id: str | None, target: str) -> str | None:
        manager = hass.data[DOMAIN].get(f"{entry_id}_tariff")
        if manager is not None and (
            manager.is_charging_active_for(target)
            or manager.is_discharging_active_for(target)
        ):
            return "Tariff Manager is charging or discharging this inverter"
        scheduler = hass.data[DOMAIN].get(f"{entry_id}_vslots", {}).get(target)
        if scheduler is not None and scheduler.owns_timer:
            return "the Virtual Slot Scheduler is driving its timer slots"
        return None

    async def _handle_backup(call: ServiceCall) -> ServiceResponse:
        serial: str = call.data["serial"]
        await _async_ensure_control_allowed(hass, call, serial)
        coordinator = _find_coordinator(hass, serial)
        if coordinator is None:
            raise ValueError(f"No Sunsynk inverter found with serial {serial!r}")
        return await async_backup(hass, coordinator, serial)

    async def _handle_restore(call: ServiceCall) -> ServiceResponse:
        serial: str = call.data["serial"]
        await _async_ensure_control_allowed(hass, call, serial)
        coordinator = _find_coordinator(hass, serial)
        if coordinator is None:
            raise ValueError(f"No Sunsynk inverter found with serial {serial!r}")
        target = coordinator.resolve_write_target(serial)
        return await async_restore(
            hass,
            coordinator,
            target,
            call.data["file"],
            dry_run=call.data["dry_run"],
            busy=_busy(_find_entry_id_for_serial(hass, serial), target),
        )

    hass.services.async_register(
        DOMAIN,
        SERVICE_BACKUP_SETTINGS,
        _handle_backup,
        _SERVICE_BACKUP_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_RESTORE_SETTINGS,
        _handle_restore,
        _SERVICE_RESTORE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )

    for service, mode, start, schema in (
        (SERVICE_CHARGE_NOW_START, MANUAL_CHARGE, True, _SERVICE_NOW_START_SCHEMA),
        (SERVICE_CHARGE_NOW_STOP, MANUAL_CHARGE, False, _SERVICE_NOW_STOP_SCHEMA),
        (
            SERVICE_DISCHARGE_NOW_START,
            MANUAL_DISCHARGE,
            True,
            _SERVICE_NOW_START_SCHEMA,
        ),
        (SERVICE_DISCHARGE_NOW_STOP, MANUAL_DISCHARGE, False, _SERVICE_NOW_STOP_SCHEMA),
    ):
        hass.services.async_register(
            DOMAIN, service, _manual_handler(mode, start), schema
        )

    hass.services.async_register(
        DOMAIN,
        SERVICE_FORCE_CHARGE,
        _handle_force_charge,
        _SERVICE_SERIAL_CURRENT_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_FORCE_DISCHARGE,
        _handle_force_discharge,
        _SERVICE_SERIAL_CURRENT_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_WORK_MODE,
        _handle_set_work_mode,
        _SERVICE_SET_WORK_MODE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_VIRTUAL_SLOT,
        _handle_set_virtual_slot,
        _SERVICE_SET_VIRTUAL_SLOT_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CLEAR_VIRTUAL_SLOT,
        _handle_clear_virtual_slot,
        _SERVICE_CLEAR_VIRTUAL_SLOT_SCHEMA,
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

    deyecloud = is_deyecloud(entry.data)
    missing = [
        key
        for key in (
            _DEYECLOUD_KEYS
            if deyecloud
            else (CONF_API_SERVER, CONF_USERNAME, CONF_PASSWORD)
        )
        if key not in entry.data
    ]
    if missing:
        # An incomplete entry crashed setup with a bare KeyError (#32). It
        # sat next to a working entry for the same account, so guessing a
        # default would have had both polling and writing the same inverter.
        raise ConfigEntryError(
            "This Sunsynk entry is incomplete (missing "
            f"{', '.join(missing)}). If you have another Sunsynk entry for the "
            "same account, delete this one; otherwise delete it and add the "
            "integration again."
        )

    coordinator: SunsynkCoordinator
    if deyecloud:
        coordinator = DeyeCloudCoordinator(
            hass,
            region=entry.data[CONF_DEYE_REGION],
            app_id=entry.data[CONF_DEYE_APP_ID],
            app_secret=entry.data[CONF_DEYE_APP_SECRET],
            login=entry.data[CONF_DEYE_LOGIN],
            password_hash=entry.data[CONF_DEYE_PASSWORD_HASH],
            company_id=entry.data.get(CONF_DEYE_COMPANY_ID),
            serials=serials,
            refresh_interval=refresh_interval,
            entry_id=entry.entry_id,
        )
    else:
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

    try:
        await coordinator.async_config_entry_first_refresh()
    except BaseException:
        # Home Assistant retries setup with a new coordinator: close this
        # one's HTTP session rather than leave it open (2.0 audit A7).
        await coordinator.async_close()
        raise

    hass.data[DOMAIN][entry.entry_id] = coordinator

    kwp = entry.options.get(CONF_PANEL_KWP, entry.data.get(CONF_PANEL_KWP))
    if kwp is not None:
        lat = entry.options.get(
            CONF_LATITUDE, entry.data.get(CONF_LATITUDE, hass.config.latitude)
        )
        lon = entry.options.get(
            CONF_LONGITUDE, entry.data.get(CONF_LONGITUDE, hass.config.longitude)
        )
        pr = entry.options.get(
            CONF_PERFORMANCE_RATIO,
            entry.data.get(CONF_PERFORMANCE_RATIO, DEFAULT_PERFORMANCE_RATIO),
        )

        def _actual_pv_energy_today() -> float | None:
            """Sum today's actual PV generation (kWh) across inverters on this entry."""
            data = coordinator.data
            if not data:
                return None
            total = 0.0
            found = False
            for serial_data in data.values():
                value = serial_data.get("pv", {}).get("etoday")
                try:
                    total += float(value)
                    found = True
                except (TypeError, ValueError):
                    continue
            return total if found else None

        calibrator = PerformanceRatioCalibrator(hass, entry.entry_id, float(pr))
        await calibrator.async_load()

        forecast_coordinator = SolarForecastCoordinator(
            hass,
            latitude=float(lat),
            longitude=float(lon),
            panel_kwp=float(kwp),
            performance_ratio=float(pr),
            calibrator=calibrator,
            actual_energy_fn=_actual_pv_energy_today,
        )
        try:
            await forecast_coordinator.async_config_entry_first_refresh()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Solar forecast initial fetch failed (will retry): %s", err)
        hass.data[DOMAIN][f"{entry.entry_id}_forecast"] = forecast_coordinator

    # Tariff manager must be created before platform setup so number.py can find it
    price_entity = entry.options.get(
        CONF_PRICE_ENTITY, entry.data.get(CONF_PRICE_ENTITY)
    )

    def _opt(key: str, default: Any = None) -> Any:
        return entry.options.get(key, entry.data.get(key, default))

    if price_entity and not deyecloud:
        tariff_manager = TariffChargingManager(
            hass=hass,
            coordinator=coordinator,
            price_entity=price_entity,
            export_price_entity=_opt(CONF_EXPORT_PRICE_ENTITY),
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
            price_max_age_minutes=(
                _opt(CONF_PRICE_MAX_AGE, DEFAULT_PRICE_MAX_AGE)
                if _opt(CONF_PRICE_STALE_CHECK, False)
                else None
            ),
            test_mode=bool(_opt(CONF_TARIFF_TEST_MODE, False)),
            negative_price_protection=bool(_opt(CONF_NEGATIVE_PRICE_PROTECTION, False)),
            entry_id=entry.entry_id,
        )
        await tariff_manager.async_load()
        hass.data[DOMAIN][f"{entry.entry_id}_tariff"] = tariff_manager

    # One scheduler per physical write target gives the service ``serial``
    # field exact semantics. Parallel slaves intentionally share their
    # master's scheduler; independent inverters never share a schedule.
    legacy_store = Store(
        hass, STORAGE_VERSION, f"{DOMAIN}_virtual_slots_{entry.entry_id}"
    )
    legacy_data = await legacy_store.async_load() or {}
    legacy_slots = legacy_data.get("slots")
    vslot_schedulers: dict[str, VirtualSlotScheduler] = {}
    for target_serial in [] if deyecloud else coordinator.write_target_serials:
        scheduler = VirtualSlotScheduler(
            hass=hass,
            coordinator=coordinator,
            entry_id=entry.entry_id,
            serial=target_serial,
            tariff_manager=hass.data[DOMAIN].get(f"{entry.entry_id}_tariff"),
            normal_charge_current=_opt(CONF_NORMAL_CHARGE_CURRENT),
            normal_discharge_current=_opt(CONF_NORMAL_DISCHARGE_CURRENT),
            max_export_power=_opt(CONF_MAX_EXPORT_POWER),
        )
        await scheduler.async_load(legacy_slots=legacy_slots)
        vslot_schedulers[target_serial] = scheduler
    if legacy_slots is not None:
        # Every current target now has its own copy. Drop the shared record so
        # an inverter added later starts empty instead of inheriting a stale
        # schedule it would immediately execute.
        await legacy_store.async_remove()
    hass.data[DOMAIN][f"{entry.entry_id}_vslots"] = vslot_schedulers
    if vslot_schedulers:
        _migrate_virtual_slot_entity_unique_ids(
            hass, entry.entry_id, next(iter(vslot_schedulers))
        )
        _adopt_stray_virtual_slot_entities(
            hass, coordinator, set(vslot_schedulers), entry.entry_id
        )

    reload_requested = False

    @callback
    def _async_check_write_targets() -> None:
        """Reload once a parallel group is recognised after setup (#21).

        Schedulers are created per write target at setup. A slave that was
        only recognised later still has its own scheduler; reloading merges
        it into the master's.
        """
        nonlocal reload_requested
        if reload_requested or not (
            set(coordinator.write_target_serials) < set(vslot_schedulers)
        ):
            return
        reload_requested = True
        _LOGGER.info("Parallel inverter group recognised, reloading to merge schedules")
        hass.async_create_task(hass.config_entries.async_reload(entry.entry_id))

    entry.async_on_unload(coordinator.async_add_listener(_async_check_write_targets))

    await hass.config_entries.async_forward_entry_setups(entry, _platforms(entry))

    # Start tariff manager after platforms are set up (switch/sensor already registered)
    tariff_manager = hass.data[DOMAIN].get(f"{entry.entry_id}_tariff")
    if tariff_manager is not None:
        tariff_manager.start()
        if _opt(CONF_TARIFF_ENABLED, False):
            tariff_manager.set_enabled(True)
    for scheduler in vslot_schedulers.values():
        scheduler.start()
        if scheduler.wanted_enabled:
            scheduler.set_enabled(True)

    if _dashboard_enabled(entry) and not deyecloud:
        await _async_enable_dashboard_frontend(hass)
        hass.async_create_task(_async_setup_dashboard(hass, entry, coordinator))

    hass.data[DOMAIN][options_snapshot_key(entry.entry_id)] = dict(entry.options)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


def _dashboard_enabled(entry: ConfigEntry) -> bool:
    """Return whether this entry explicitly opted into Lovelace mutation."""
    return bool(
        entry.options.get(
            CONF_CREATE_DASHBOARD,
            entry.data.get(CONF_CREATE_DASHBOARD, False),
        )
    )


async def _async_enable_dashboard_frontend(hass: HomeAssistant) -> None:
    """Register frontend resources only for an opted-in config entry."""
    try:
        from homeassistant.components.frontend import add_extra_js_url

        add_extra_js_url(hass, _CARD_RESOURCE_URL)
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("Could not register extra frontend JS module: %s", err)
    await _async_register_lovelace_resource(hass)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload safely, restoring settings before the API session is closed."""
    domain_data = hass.data[DOMAIN]
    coordinator: SunsynkCoordinator = domain_data[entry.entry_id]
    forecast_coordinator: SolarForecastCoordinator | None = domain_data.get(
        f"{entry.entry_id}_forecast"
    )
    tariff_manager: TariffChargingManager | None = domain_data.get(
        f"{entry.entry_id}_tariff"
    )
    vslot_schedulers: dict[str, VirtualSlotScheduler] = domain_data.get(
        f"{entry.entry_id}_vslots", {}
    )

    # Freeze automation before entities are removed. If platform unload is
    # rejected, restart the listeners and leave the integration operational.
    if tariff_manager is not None:
        tariff_manager.stop()
    for scheduler in vslot_schedulers.values():
        scheduler.stop()

    unload_ok = await hass.config_entries.async_unload_platforms(
        entry, _platforms(entry)
    )
    if not unload_ok:
        if tariff_manager is not None:
            tariff_manager.start()
        for scheduler in vslot_schedulers.values():
            scheduler.start()
        return False

    # Every component gets a restoration attempt even if another component
    # fails. Virtual-slot restoration runs last so the exact pre-ownership
    # slot values and configured normal currents are the final state.
    if tariff_manager is not None:
        try:
            await tariff_manager.async_shutdown()
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Tariff shutdown failed during unload: %s", err)
    for serial, scheduler in vslot_schedulers.items():
        try:
            await scheduler.async_shutdown()
        except Exception as err:  # noqa: BLE001
            _LOGGER.error(
                "Virtual slot shutdown failed for %s during unload: %s",
                serial,
                err,
            )

    await coordinator.async_close()
    if forecast_coordinator is not None:
        await forecast_coordinator.async_close()

    domain_data.pop(entry.entry_id, None)
    domain_data.pop(f"{entry.entry_id}_forecast", None)
    domain_data.pop(f"{entry.entry_id}_tariff", None)
    domain_data.pop(f"{entry.entry_id}_vslots", None)
    domain_data.pop(options_snapshot_key(entry.entry_id), None)
    return True


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
    reg = er.async_get(hass)
    uid_map: dict[str, str] = {
        e.unique_id[len(first_serial) + 1 :]: e.entity_id
        for e in reg.entities.values()
        if e.platform == DOMAIN and e.unique_id.startswith(f"{first_serial}_")
    }

    def eid(key: str) -> str | None:
        return uid_map.get(key)

    forecast_prefix = f"{entry.entry_id}_forecast_"
    forecast_uid_map: dict[str, str] = {
        e.unique_id[len(forecast_prefix) :]: e.entity_id
        for e in reg.entities.values()
        if e.platform == DOMAIN and e.unique_id.startswith(forecast_prefix)
    }
    forecast_eid_fn = (
        (lambda key: forecast_uid_map.get(key)) if forecast_uid_map else None
    )

    tariff_prefix = f"{entry.entry_id}_tariff_"
    tariff_uid_map: dict[str, str] = {
        e.unique_id[len(tariff_prefix) :]: e.entity_id
        for e in reg.entities.values()
        if e.platform == DOMAIN and e.unique_id.startswith(tariff_prefix)
    }
    tariff_eid_fn = (lambda key: tariff_uid_map.get(key)) if tariff_uid_map else None

    first_target = (
        coordinator.resolve_write_target(first_serial) if first_serial else ""
    )
    vslot_prefix = f"{first_target}_vslots_"
    vslot_uid_map: dict[str, str] = {
        e.unique_id[len(vslot_prefix) :]: e.entity_id
        for e in reg.entities.values()
        if e.platform == DOMAIN and e.unique_id.startswith(vslot_prefix)
    }
    vslot_eid_fn = (lambda key: vslot_uid_map.get(key)) if vslot_uid_map else None

    dashboard_config = build_dashboard(
        prefix, eid, forecast_eid_fn, tariff_eid_fn, entry.entry_id, vslot_eid_fn
    )

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

            await Store(hass, 1, f"lovelace.{url_path}").async_save(
                {"config": dashboard_config}
            )
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
            if not any(
                isinstance(v, dict) and v.get("url_path") == url_path for v in items
            ):
                items.append({"id": uuid.uuid4().hex, **_item})
                data["items"] = items
                await ds.async_save(data)
            _LOGGER.warning(
                "Sunsynk: dashboard saved to storage — restart HA to see it in sidebar"
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Sunsynk: lovelace_dashboards write failed: %s", err)


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload on a Configure change, not on a value saved live by an entity."""
    snapshot = hass.data.get(DOMAIN, {}).get(options_snapshot_key(entry.entry_id))
    if snapshot == dict(entry.options):
        return
    await hass.config_entries.async_reload(entry.entry_id)

"""Config flow for Sunsynk integration."""

from __future__ import annotations

import logging
import math
from typing import Any

import aiohttp
import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.selector import EntitySelector, EntitySelectorConfig

from .api.auth import SunsynkAuth, SunsynkAuthError
from .const import (
    API_SERVER_SUNSYNK,
    API_SERVERS,
    BACKEND_DEYECLOUD,
    CONF_ADMIN_ONLY_CONTROL,
    CONF_API_SERVER,
    CONF_BACKEND,
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
    CONF_MANUAL_CHARGE_CURRENT,
    CONF_MANUAL_DISCHARGE_CURRENT,
    CONF_MANUAL_DISCHARGE_POWER,
    CONF_MANUAL_MIN_SOC,
    CONF_MANUAL_TARGET_SOC,
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
    DEYE_REGIONS,
    DOMAIN,
    MAX_REFRESH_INTERVAL,
    MIN_REFRESH_INTERVAL,
    is_deyecloud,
)
from .current_limits import current_limit, describe_limit, group_sizes
from .deyecloud.client import (
    DeyeCloudApiError,
    DeyeCloudAuthError,
    DeyeCloudClient,
    hash_password,
)
from .write_validation import MAX_CURRENT_A, MAX_POWER_W

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_API_SERVER, default=API_SERVER_SUNSYNK): vol.In(
            list(API_SERVERS.values())
        ),
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Required(CONF_SERIALS): str,
        vol.Optional(CONF_REFRESH_INTERVAL, default=DEFAULT_REFRESH_INTERVAL): vol.All(
            vol.Coerce(int),
            vol.Range(min=MIN_REFRESH_INTERVAL, max=MAX_REFRESH_INTERVAL),
        ),
    }
)


# Charge / discharge now defaults, with the range each may take.
_MANUAL_FIELDS: dict[str, tuple[int, int]] = {
    CONF_MANUAL_CHARGE_CURRENT: (1, MAX_CURRENT_A),
    CONF_MANUAL_TARGET_SOC: (1, 100),
    CONF_MANUAL_DISCHARGE_CURRENT: (1, MAX_CURRENT_A),
    CONF_MANUAL_MIN_SOC: (0, 100),
    CONF_MANUAL_DISCHARGE_POWER: (1, MAX_POWER_W),
}

_REFRESH_INTERVAL_FIELD = vol.All(
    vol.Coerce(int),
    vol.Range(min=MIN_REFRESH_INTERVAL, max=MAX_REFRESH_INTERVAL),
)

STEP_DEYECLOUD_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_DEYE_REGION, default="eu"): vol.In(list(DEYE_REGIONS)),
        vol.Required(CONF_DEYE_APP_ID): str,
        vol.Required(CONF_DEYE_APP_SECRET): str,
        vol.Required(CONF_DEYE_LOGIN): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(CONF_DEYE_COMPANY_ID): str,
        vol.Optional(
            CONF_REFRESH_INTERVAL, default=DEFAULT_REFRESH_INTERVAL
        ): _REFRESH_INTERVAL_FIELD,
    }
)


async def _async_list_deyecloud_inverters(data: dict[str, Any]) -> list[dict]:
    """Log in to Deye Cloud and list the account's inverters."""
    async with aiohttp.ClientSession() as session:
        client = DeyeCloudClient(
            session,
            data[CONF_DEYE_REGION],
            data[CONF_DEYE_APP_ID],
            data[CONF_DEYE_APP_SECRET],
            data[CONF_DEYE_LOGIN],
            data[CONF_DEYE_PASSWORD_HASH],
            company_id=data.get(CONF_DEYE_COMPANY_ID),
        )
        return await client.async_list_inverters()


def _inverter_label(inverter: dict[str, Any]) -> str:
    parts = [str(inverter["deviceSn"])]
    if inverter.get("stationName"):
        parts.append(str(inverter["stationName"]))
    return " — ".join(parts)


async def _async_validate_credentials(
    api_server: str,
    username: str,
    password: str,
) -> None:
    """Validate Sunsynk credentials by requesting a token."""
    auth = SunsynkAuth(
        api_server=api_server,
        username=username,
        password=password,
    )
    async with aiohttp.ClientSession() as session:
        await auth.async_get_token(session)


class SunsynkConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle config flow for Sunsynk."""

    VERSION = 1

    def __init__(self) -> None:
        self._deyecloud: dict[str, Any] = {}
        self._deyecloud_inverters: dict[str, str] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Choose the cloud the inverters report to."""
        if user_input is not None:
            # A Sunsynk form submitted to the first step, as before 2.1.
            return await self.async_step_sunsynk(user_input)
        return self.async_show_menu(
            step_id="user", menu_options=["sunsynk", "deyecloud"]
        )

    async def async_step_sunsynk(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            serials = [
                s.strip() for s in user_input[CONF_SERIALS].split(";") if s.strip()
            ]
            if not serials:
                errors[CONF_SERIALS] = "invalid_serials"
            else:
                try:
                    await _async_validate_credentials(
                        user_input[CONF_API_SERVER],
                        user_input[CONF_USERNAME],
                        user_input[CONF_PASSWORD],
                    )
                except SunsynkAuthError:
                    errors["base"] = "invalid_auth"
                except Exception:
                    _LOGGER.exception("Unexpected error during config flow")
                    errors["base"] = "cannot_connect"
                else:
                    unique_id = (
                        f"{user_input[CONF_API_SERVER]}_{user_input[CONF_USERNAME]}"
                    )
                    await self.async_set_unique_id(unique_id)
                    self._abort_if_unique_id_configured()

                    return self.async_create_entry(
                        title=f"Sunsynk ({', '.join(serials)})",
                        data={
                            CONF_API_SERVER: user_input[CONF_API_SERVER],
                            CONF_USERNAME: user_input[CONF_USERNAME],
                            CONF_PASSWORD: user_input[CONF_PASSWORD],
                            CONF_SERIALS: serials,
                            CONF_REFRESH_INTERVAL: user_input[CONF_REFRESH_INTERVAL],
                        },
                    )

        return self.async_show_form(
            step_id="sunsynk",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
            description_placeholders={
                "api_sunsynk": API_SERVER_SUNSYNK,
                "serial_hint": "Separate multiple serials with semicolons: SN1;SN2",
            },
        )

    async def async_step_deyecloud(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Deye Cloud: the user's developer app and Deye Cloud login."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {
                CONF_BACKEND: BACKEND_DEYECLOUD,
                CONF_DEYE_REGION: user_input[CONF_DEYE_REGION],
                # Pasted values often carry a stray space (#37).
                CONF_DEYE_APP_ID: user_input[CONF_DEYE_APP_ID].strip(),
                CONF_DEYE_APP_SECRET: user_input[CONF_DEYE_APP_SECRET].strip(),
                CONF_DEYE_LOGIN: user_input[CONF_DEYE_LOGIN].strip(),
                CONF_DEYE_PASSWORD_HASH: hash_password(user_input[CONF_PASSWORD]),
                CONF_REFRESH_INTERVAL: user_input[CONF_REFRESH_INTERVAL],
            }
            company_id = (user_input.get(CONF_DEYE_COMPANY_ID) or "").strip()
            if company_id:
                data[CONF_DEYE_COMPANY_ID] = company_id
            try:
                inverters = await _async_list_deyecloud_inverters(data)
            except DeyeCloudAuthError:
                errors["base"] = "invalid_auth_deyecloud"
            except DeyeCloudApiError:
                errors["base"] = "cannot_connect_deyecloud"
            except Exception:
                _LOGGER.exception("Unexpected error during Deye Cloud setup")
                errors["base"] = "cannot_connect_deyecloud"
            else:
                if not inverters:
                    errors["base"] = "no_inverters"
                else:
                    await self.async_set_unique_id(
                        f"{BACKEND_DEYECLOUD}_{data[CONF_DEYE_REGION]}_"
                        f"{data[CONF_DEYE_LOGIN].lower()}"
                    )
                    self._abort_if_unique_id_configured()
                    self._deyecloud = data
                    self._deyecloud_inverters = {
                        str(inverter["deviceSn"]): _inverter_label(inverter)
                        for inverter in inverters
                    }
                    return await self.async_step_deyecloud_inverters()

        return self.async_show_form(
            step_id="deyecloud",
            data_schema=self.add_suggested_values_to_schema(
                STEP_DEYECLOUD_SCHEMA,
                {
                    key: value
                    for key, value in (user_input or {}).items()
                    if key != CONF_PASSWORD
                },
            ),
            errors=errors,
        )

    async def async_step_deyecloud_inverters(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Pick the inverters to add from those on the Deye Cloud account."""
        errors: dict[str, str] = {}
        if user_input is not None:
            serials = [
                serial
                for serial in self._deyecloud_inverters
                if serial in user_input[CONF_SERIALS]
            ]
            if serials:
                return self.async_create_entry(
                    title=f"Deye Cloud ({', '.join(serials)})",
                    data={**self._deyecloud, CONF_SERIALS: serials},
                )
            errors[CONF_SERIALS] = "invalid_serials"

        return self.async_show_form(
            step_id="deyecloud_inverters",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SERIALS, default=list(self._deyecloud_inverters)
                    ): cv.multi_select(self._deyecloud_inverters),
                }
            ),
            errors=errors,
        )

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> SunsynkOptionsFlow:
        return SunsynkOptionsFlow(config_entry)


class SunsynkOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for Sunsynk."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if is_deyecloud(self._config_entry.data):
            return await self.async_step_deyecloud(user_input)
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}
        opts = self._config_entry.options
        data = self._config_entry.data

        if user_input is not None:
            serials_raw = user_input.get(CONF_SERIALS, "")
            serials = [s.strip() for s in serials_raw.split(";") if s.strip()]
            api_server = user_input.get(
                CONF_API_SERVER,
                data.get(CONF_API_SERVER, API_SERVER_SUNSYNK),
            )
            username = str(
                user_input.get(CONF_USERNAME, data.get(CONF_USERNAME, ""))
            ).strip()
            new_password = user_input.get(CONF_PASSWORD) or ""
            password = new_password or data.get(CONF_PASSWORD, "")
            if not serials:
                errors[CONF_SERIALS] = "invalid_serials"
            if not username or not password:
                errors["base"] = "invalid_auth"
            else:
                kwp_raw = str(user_input.get(CONF_PANEL_KWP, "")).strip()

                forecast_fields: dict[str, Any] = {}
                if kwp_raw:
                    try:
                        kwp = float(kwp_raw)
                        if not math.isfinite(kwp) or kwp <= 0:
                            raise ValueError
                        forecast_fields[CONF_PANEL_KWP] = kwp
                        lat_raw = str(user_input.get(CONF_LATITUDE, "")).strip()
                        lon_raw = str(user_input.get(CONF_LONGITUDE, "")).strip()
                        latitude = (
                            float(lat_raw)
                            if lat_raw
                            else float(self.hass.config.latitude)
                        )
                        longitude = (
                            float(lon_raw)
                            if lon_raw
                            else float(self.hass.config.longitude)
                        )
                        pr_raw = str(user_input.get(CONF_PERFORMANCE_RATIO, "")).strip()
                        performance_ratio = (
                            float(pr_raw) if pr_raw else DEFAULT_PERFORMANCE_RATIO
                        )
                        if not (
                            math.isfinite(latitude)
                            and -90 <= latitude <= 90
                            and math.isfinite(longitude)
                            and -180 <= longitude <= 180
                            and math.isfinite(performance_ratio)
                            and 0 < performance_ratio <= 1
                        ):
                            raise ValueError
                        forecast_fields[CONF_LATITUDE] = latitude
                        forecast_fields[CONF_LONGITUDE] = longitude
                        forecast_fields[CONF_PERFORMANCE_RATIO] = performance_ratio
                    except (ValueError, TypeError):
                        errors["base"] = "invalid_forecast_config"

                export_fields: dict[str, Any] = {}
                export_raw = str(user_input.get(CONF_MAX_EXPORT_POWER, "")).strip()
                if export_raw:
                    try:
                        export_power = int(export_raw)
                        if not 1 <= export_power <= MAX_POWER_W:
                            raise ValueError
                        export_fields[CONF_MAX_EXPORT_POWER] = export_power
                    except ValueError:
                        errors[CONF_MAX_EXPORT_POWER] = "invalid_export_power"

                tariff_fields: dict[str, Any] = {}
                price_entity = user_input.get(CONF_PRICE_ENTITY, "")
                if price_entity:
                    tariff_fields[CONF_PRICE_ENTITY] = price_entity
                    export_price_entity = user_input.get(CONF_EXPORT_PRICE_ENTITY, "")
                    if export_price_entity:
                        tariff_fields[CONF_EXPORT_PRICE_ENTITY] = export_price_entity
                    try:
                        tariff_fields.update(self._parse_tariff_fields(user_input))
                    except (ValueError, TypeError):
                        errors["base"] = "invalid_tariff_config"
                    else:
                        self._check_current_limits(tariff_fields, errors, placeholders)

                if not errors:
                    credentials_changed = (
                        api_server != data.get(CONF_API_SERVER)
                        or username != data.get(CONF_USERNAME)
                        or bool(new_password)
                    )
                    if credentials_changed:
                        unique_id = f"{api_server}_{username}"
                        for entry in self.hass.config_entries.async_entries(DOMAIN):
                            if (
                                entry.entry_id != self._config_entry.entry_id
                                and entry.unique_id == unique_id
                            ):
                                errors["base"] = "already_configured"
                                break

                    if credentials_changed and not errors:
                        try:
                            await _async_validate_credentials(
                                api_server,
                                username,
                                password,
                            )
                        except SunsynkAuthError:
                            errors["base"] = "invalid_auth"
                        except Exception:
                            _LOGGER.exception(
                                "Unexpected error while updating Sunsynk credentials"
                            )
                            errors["base"] = "cannot_connect"

                if not errors:
                    if credentials_changed:
                        new_data = dict(data)
                        new_data.update(
                            {
                                CONF_API_SERVER: api_server,
                                CONF_USERNAME: username,
                                CONF_PASSWORD: password,
                            }
                        )
                        self.hass.config_entries.async_update_entry(
                            self._config_entry,
                            data=new_data,
                            unique_id=f"{api_server}_{username}",
                        )

                    return self.async_create_entry(
                        title="",
                        data={
                            CONF_REFRESH_INTERVAL: user_input[CONF_REFRESH_INTERVAL],
                            CONF_SERIALS: serials,
                            CONF_CREATE_DASHBOARD: user_input.get(
                                CONF_CREATE_DASHBOARD, False
                            ),
                            CONF_ADMIN_ONLY_CONTROL: user_input.get(
                                CONF_ADMIN_ONLY_CONTROL, False
                            ),
                            **forecast_fields,
                            **export_fields,
                            **tariff_fields,
                            # Saved by the Tariff Manager switch, not a form field.
                            **(
                                {CONF_TARIFF_ENABLED: opts[CONF_TARIFF_ENABLED]}
                                if CONF_TARIFF_ENABLED in opts and tariff_fields
                                else {}
                            ),
                        },
                    )

        current_serials = opts.get(CONF_SERIALS, data.get(CONF_SERIALS, []))
        current_refresh = opts.get(
            CONF_REFRESH_INTERVAL,
            data.get(CONF_REFRESH_INTERVAL, DEFAULT_REFRESH_INTERVAL),
        )
        current_lat = opts.get(
            CONF_LATITUDE, data.get(CONF_LATITUDE, self.hass.config.latitude)
        )
        current_lon = opts.get(
            CONF_LONGITUDE, data.get(CONF_LONGITUDE, self.hass.config.longitude)
        )
        current_kwp = opts.get(CONF_PANEL_KWP, data.get(CONF_PANEL_KWP, ""))
        current_pr = opts.get(
            CONF_PERFORMANCE_RATIO, data.get(CONF_PERFORMANCE_RATIO, "")
        )

        def _opt(key: str, default: Any = "") -> Any:
            return opts.get(key, data.get(key, default))

        def _opt_str(key: str) -> str | None:
            # Tariff fields are stored as int/float once saved (see
            # _parse_tariff_fields), but redisplayed here as plain `str`
            # form fields. Shown as a suggested value, not a default: Home
            # Assistant leaves a cleared field out of the submission, and a
            # default would then put the old value straight back, so a field
            # could never be cleared (#21: cleared active hours saved as 0).
            value = _opt(key, "")
            return None if value in ("", None) else str(value)

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_API_SERVER,
                    default=data.get(CONF_API_SERVER, API_SERVER_SUNSYNK),
                ): vol.In(list(API_SERVERS.values())),
                vol.Required(
                    CONF_USERNAME,
                    default=data.get(CONF_USERNAME, ""),
                ): str,
                vol.Optional(CONF_PASSWORD, default=""): str,
                vol.Required(CONF_SERIALS, default=";".join(current_serials)): str,
                vol.Optional(CONF_REFRESH_INTERVAL, default=current_refresh): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_REFRESH_INTERVAL, max=MAX_REFRESH_INTERVAL),
                ),
                vol.Optional(
                    CONF_CREATE_DASHBOARD,
                    default=bool(_opt(CONF_CREATE_DASHBOARD, False)),
                ): cv.boolean,
                vol.Optional(
                    CONF_ADMIN_ONLY_CONTROL,
                    default=bool(_opt(CONF_ADMIN_ONLY_CONTROL, False)),
                ): cv.boolean,
                # Solar Forecast
                vol.Optional(
                    CONF_LATITUDE, default=str(current_lat) if current_lat != "" else ""
                ): str,
                vol.Optional(
                    CONF_LONGITUDE,
                    default=str(current_lon) if current_lon != "" else "",
                ): str,
                vol.Optional(
                    CONF_PANEL_KWP,
                    default=str(current_kwp) if current_kwp != "" else "",
                ): str,
                vol.Optional(
                    CONF_PERFORMANCE_RATIO,
                    default=str(current_pr)
                    if current_pr != ""
                    else str(DEFAULT_PERFORMANCE_RATIO),
                ): str,
                # Tariff — price sensor(s). Export defaults to the same entity as
                # import when left blank, so single-price setups are unaffected.
                vol.Optional(
                    CONF_PRICE_ENTITY,
                    description={"suggested_value": _opt(CONF_PRICE_ENTITY) or None},
                ): EntitySelector(
                    EntitySelectorConfig(domain=["sensor", "input_number"])
                ),
                vol.Optional(
                    CONF_EXPORT_PRICE_ENTITY,
                    description={
                        "suggested_value": _opt(CONF_EXPORT_PRICE_ENTITY) or None
                    },
                ): EntitySelector(
                    EntitySelectorConfig(domain=["sensor", "input_number"])
                ),
                # Cheap-rate charging
                vol.Optional(
                    CONF_CHEAP_THRESHOLD,
                    description={"suggested_value": _opt_str(CONF_CHEAP_THRESHOLD)},
                ): str,
                vol.Optional(
                    CONF_CHEAP_CHARGE_CURRENT,
                    description={
                        "suggested_value": _opt_str(CONF_CHEAP_CHARGE_CURRENT)
                    },
                ): str,
                vol.Optional(
                    CONF_NORMAL_CHARGE_CURRENT,
                    description={
                        "suggested_value": _opt_str(CONF_NORMAL_CHARGE_CURRENT)
                    },
                ): str,
                vol.Optional(
                    CONF_CHEAP_TARGET_SOC,
                    default=_opt(CONF_CHEAP_TARGET_SOC, DEFAULT_CHEAP_TARGET_SOC),
                ): vol.All(vol.Coerce(int), vol.Range(min=10, max=100)),
                # Expensive-rate discharging
                vol.Optional(
                    CONF_EXPENSIVE_THRESHOLD,
                    description={"suggested_value": _opt_str(CONF_EXPENSIVE_THRESHOLD)},
                ): str,
                vol.Optional(
                    CONF_PEAK_DISCHARGE_CURRENT,
                    description={
                        "suggested_value": _opt_str(CONF_PEAK_DISCHARGE_CURRENT)
                    },
                ): str,
                vol.Optional(
                    CONF_NORMAL_DISCHARGE_CURRENT,
                    description={
                        "suggested_value": _opt_str(CONF_NORMAL_DISCHARGE_CURRENT)
                    },
                ): str,
                vol.Optional(
                    CONF_DISCHARGE_MIN_SOC,
                    default=_opt(CONF_DISCHARGE_MIN_SOC, DEFAULT_DISCHARGE_MIN_SOC),
                ): vol.All(vol.Coerce(int), vol.Range(min=0, max=90)),
                vol.Optional(
                    CONF_MAX_EXPORT_POWER,
                    description={"suggested_value": _opt_str(CONF_MAX_EXPORT_POWER)},
                ): str,
                # Scheduler (both blank = always active)
                vol.Optional(
                    CONF_TARIFF_START_HOUR,
                    description={"suggested_value": _opt_str(CONF_TARIFF_START_HOUR)},
                ): str,
                vol.Optional(
                    CONF_TARIFF_END_HOUR,
                    description={"suggested_value": _opt_str(CONF_TARIFF_END_HOUR)},
                ): str,
                # Price quality
                vol.Optional(
                    CONF_PRICE_STALE_CHECK,
                    default=bool(_opt(CONF_PRICE_STALE_CHECK, False)),
                ): bool,
                vol.Optional(
                    CONF_TARIFF_TEST_MODE,
                    default=bool(_opt(CONF_TARIFF_TEST_MODE, False)),
                ): bool,
                vol.Optional(
                    CONF_NEGATIVE_PRICE_PROTECTION,
                    default=bool(_opt(CONF_NEGATIVE_PRICE_PROTECTION, False)),
                ): bool,
                # Charge / discharge now (blank = the Tariff Manager values)
                **{
                    vol.Optional(
                        key, description={"suggested_value": _opt_str(key)}
                    ): str
                    for key in _MANUAL_FIELDS
                },
                vol.Optional(
                    CONF_PRICE_MAX_AGE,
                    default=_opt(CONF_PRICE_MAX_AGE, DEFAULT_PRICE_MAX_AGE),
                ): vol.All(vol.Coerce(int), vol.Range(min=5, max=1440)),
            }
        )

        placeholders.update(self._export_placeholders(_opt(CONF_MAX_EXPORT_POWER)))
        if user_input is not None:
            # Shown again after an error: keep what was just typed, not the
            # saved values, so the field the error points at shows the value
            # that was refused. The password is never sent back to the page.
            schema = self.add_suggested_values_to_schema(
                schema,
                {k: v for k, v in user_input.items() if k != CONF_PASSWORD},
            )
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_deyecloud(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Deye Cloud entries are read-only for now: only how often to poll."""
        if user_input is not None:
            return self.async_create_entry(
                title="",
                data={
                    **self._config_entry.options,
                    CONF_REFRESH_INTERVAL: user_input[CONF_REFRESH_INTERVAL],
                },
            )
        current = self._config_entry.options.get(
            CONF_REFRESH_INTERVAL,
            self._config_entry.data.get(
                CONF_REFRESH_INTERVAL, DEFAULT_REFRESH_INTERVAL
            ),
        )
        return self.async_show_form(
            step_id="deyecloud",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_REFRESH_INTERVAL, default=current
                    ): _REFRESH_INTERVAL_FIELD,
                }
            ),
        )

    def _export_placeholders(self, saved_limit: Any) -> dict[str, str]:
        """How the saved maximum export power splits over the inverters.

        A form can't recalculate while someone types, so the description
        shows the split of the value saved last time.
        """
        coordinator = self.hass.data.get(DOMAIN, {}).get(self._config_entry.entry_id)
        inverters = (
            max(group_sizes(coordinator).values(), default=1) if coordinator else 1
        )
        try:
            share = str(int(saved_limit) // inverters) if saved_limit else "-"
        except (TypeError, ValueError):
            share = "-"
        return {"export_inverters": str(inverters), "export_share": share}

    def _check_current_limits(
        self,
        tariff_fields: dict[str, Any],
        errors: dict[str, str],
        placeholders: dict[str, str],
    ) -> None:
        """Refuse Tariff Manager currents above what the hardware will use.

        Checked against the inverters' last poll, so only while the
        integration is running; a value the hardware reports as unknown is
        never a reason to refuse.
        """
        coordinator = self.hass.data.get(DOMAIN, {}).get(self._config_entry.entry_id)
        if coordinator is None:
            return
        serials = list(coordinator.write_target_serials)
        for direction, fields in (
            (
                "charge",
                (
                    CONF_CHEAP_CHARGE_CURRENT,
                    CONF_NORMAL_CHARGE_CURRENT,
                    CONF_MANUAL_CHARGE_CURRENT,
                ),
            ),
            (
                "discharge",
                (
                    CONF_PEAK_DISCHARGE_CURRENT,
                    CONF_NORMAL_DISCHARGE_CURRENT,
                    CONF_MANUAL_DISCHARGE_CURRENT,
                ),
            ),
        ):
            limit = current_limit(
                coordinator.data, serials, direction, group_sizes(coordinator)
            )
            if limit.maximum is None:
                continue
            for field in fields:
                value = tariff_fields.get(field)
                if value is not None and value > limit.maximum:
                    errors[field] = f"{direction}_current_above_limit"
                    placeholders[f"{direction}_max"] = str(limit.maximum)
                    placeholders[f"{direction}_limits"] = describe_limit(limit)

    @staticmethod
    def _parse_tariff_fields(user_input: dict[str, Any]) -> dict[str, Any]:
        """Parse and validate optional tariff numeric fields."""
        result: dict[str, Any] = {}

        def _parse_float(key: str) -> float | None:
            raw = str(user_input.get(key, "")).strip()
            if not raw:
                return None
            value = float(raw)
            if not math.isfinite(value):
                raise ValueError(f"{key} must be finite")
            return value

        def _parse_int(key: str) -> int | None:
            raw = str(user_input.get(key, "")).strip()
            return int(raw) if raw else None

        cheap_thr = _parse_float(CONF_CHEAP_THRESHOLD)
        cheap_amp = _parse_int(CONF_CHEAP_CHARGE_CURRENT)
        normal_amp = _parse_int(CONF_NORMAL_CHARGE_CURRENT)

        if any(v is not None for v in (cheap_thr, cheap_amp, normal_amp)):
            if not all(v is not None for v in (cheap_thr, cheap_amp, normal_amp)):
                raise ValueError(
                    "Cheap charging requires threshold, charge current, and normal current"
                )
            if (
                not 1 <= cheap_amp <= MAX_CURRENT_A
                or not 1 <= normal_amp <= MAX_CURRENT_A
            ):  # type: ignore[operator]
                raise ValueError(
                    f"Charge currents must be positive and no greater than {MAX_CURRENT_A} A"
                )
            result[CONF_CHEAP_THRESHOLD] = cheap_thr
            result[CONF_CHEAP_CHARGE_CURRENT] = cheap_amp
            result[CONF_NORMAL_CHARGE_CURRENT] = normal_amp
            result[CONF_CHEAP_TARGET_SOC] = user_input.get(
                CONF_CHEAP_TARGET_SOC, DEFAULT_CHEAP_TARGET_SOC
            )

        exp_thr = _parse_float(CONF_EXPENSIVE_THRESHOLD)
        peak_amp = _parse_int(CONF_PEAK_DISCHARGE_CURRENT)
        normal_dis_amp = _parse_int(CONF_NORMAL_DISCHARGE_CURRENT)

        if any(v is not None for v in (exp_thr, peak_amp, normal_dis_amp)):
            if not all(v is not None for v in (exp_thr, peak_amp, normal_dis_amp)):
                raise ValueError(
                    "Discharge requires threshold, peak discharge current, and normal discharge current"
                )
            if (
                not 1 <= peak_amp <= MAX_CURRENT_A
                or not 1 <= normal_dis_amp <= MAX_CURRENT_A
            ):  # type: ignore[operator]
                raise ValueError(
                    "Discharge currents must be positive and no greater than "
                    f"{MAX_CURRENT_A} A"
                )
            result[CONF_EXPENSIVE_THRESHOLD] = exp_thr
            result[CONF_PEAK_DISCHARGE_CURRENT] = peak_amp
            result[CONF_NORMAL_DISCHARGE_CURRENT] = normal_dis_amp
            result[CONF_DISCHARGE_MIN_SOC] = user_input.get(
                CONF_DISCHARGE_MIN_SOC, DEFAULT_DISCHARGE_MIN_SOC
            )

        start_raw = str(user_input.get(CONF_TARIFF_START_HOUR, "")).strip()
        end_raw = str(user_input.get(CONF_TARIFF_END_HOUR, "")).strip()
        if start_raw or end_raw:
            if not (start_raw and end_raw):
                raise ValueError("Schedule requires both start hour and end hour")
            start_h, end_h = int(start_raw), int(end_raw)
            if not (0 <= start_h <= 23 and 0 <= end_h <= 23):
                raise ValueError("Hours must be 0–23")
            result[CONF_TARIFF_START_HOUR] = start_h
            result[CONF_TARIFF_END_HOUR] = end_h

        result[CONF_PRICE_MAX_AGE] = user_input.get(
            CONF_PRICE_MAX_AGE, DEFAULT_PRICE_MAX_AGE
        )
        result[CONF_PRICE_STALE_CHECK] = bool(
            user_input.get(CONF_PRICE_STALE_CHECK, False)
        )
        result[CONF_TARIFF_TEST_MODE] = bool(
            user_input.get(CONF_TARIFF_TEST_MODE, False)
        )
        result[CONF_NEGATIVE_PRICE_PROTECTION] = bool(
            user_input.get(CONF_NEGATIVE_PRICE_PROTECTION, False)
        )
        for key, (low, high) in _MANUAL_FIELDS.items():
            value = _parse_int(key)
            if value is None:
                continue
            if not low <= value <= high:
                raise ValueError(f"{key} must be {low}-{high}")
            result[key] = value
        return result

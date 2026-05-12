"""Config flow for Sunsynk integration."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResult

from .api.auth import SunsynkAuth, SunsynkAuthError
from .const import (
    API_SERVER_SUNSYNK,
    API_SERVERS,
    CONF_API_SERVER,
    CONF_REFRESH_INTERVAL,
    CONF_SERIALS,
    DEFAULT_REFRESH_INTERVAL,
    DOMAIN,
    MAX_REFRESH_INTERVAL,
    MIN_REFRESH_INTERVAL,
)

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


class SunsynkConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle config flow for Sunsynk."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            serials = [s.strip() for s in user_input[CONF_SERIALS].split(";") if s.strip()]
            if not serials:
                errors[CONF_SERIALS] = "invalid_serials"
            else:
                try:
                    auth = SunsynkAuth(
                        api_server=user_input[CONF_API_SERVER],
                        username=user_input[CONF_USERNAME],
                        password=user_input[CONF_PASSWORD],
                    )
                    async with aiohttp.ClientSession() as session:
                        await auth.async_get_token(session)
                except SunsynkAuthError:
                    errors["base"] = "invalid_auth"
                except Exception:  # noqa: BLE001
                    _LOGGER.exception("Unexpected error during config flow")
                    errors["base"] = "cannot_connect"
                else:
                    unique_id = f"{user_input[CONF_API_SERVER]}_{user_input[CONF_USERNAME]}"
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
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
            description_placeholders={
                "api_sunsynk": API_SERVER_SUNSYNK,
                "serial_hint": "Separate multiple serials with semicolons: SN1;SN2",
            },
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
        errors: dict[str, str] = {}

        if user_input is not None:
            serials_raw = user_input.get(CONF_SERIALS, "")
            serials = [s.strip() for s in serials_raw.split(";") if s.strip()]
            if not serials:
                errors[CONF_SERIALS] = "invalid_serials"
            else:
                return self.async_create_entry(
                    title="",
                    data={
                        CONF_REFRESH_INTERVAL: user_input[CONF_REFRESH_INTERVAL],
                        CONF_SERIALS: serials,
                    },
                )

        current_serials = self._config_entry.data.get(CONF_SERIALS, [])
        current_refresh = self._config_entry.options.get(
            CONF_REFRESH_INTERVAL,
            self._config_entry.data.get(CONF_REFRESH_INTERVAL, DEFAULT_REFRESH_INTERVAL),
        )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SERIALS, default=";".join(current_serials)
                ): str,
                vol.Optional(
                    CONF_REFRESH_INTERVAL, default=current_refresh
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_REFRESH_INTERVAL, max=MAX_REFRESH_INTERVAL),
                ),
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
        )

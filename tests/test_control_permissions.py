"""Actions require a user who may control the inverter (security fix)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import Unauthorized, UnknownUser

from custom_components.sunsynk import _async_ensure_control_allowed


def _hass(user):
    hass = MagicMock()
    hass.auth.async_get_user = AsyncMock(return_value=user)
    return hass


def _call(user_id="u1"):
    return SimpleNamespace(context=SimpleNamespace(user_id=user_id), data={})


def _user(admin=False, allowed=()):
    user = MagicMock(is_admin=admin)
    user.permissions.check_entity.side_effect = lambda entity_id, _p: entity_id in allowed
    return user


def _registries(device=True, entities=("switch.inv_x",)):
    dev = SimpleNamespace(id="dev1") if device else None
    return (
        patch(
            "custom_components.sunsynk.dr.async_get",
            return_value=MagicMock(async_get_device=MagicMock(return_value=dev)),
        ),
        patch(
            "custom_components.sunsynk.er.async_entries_for_device",
            return_value=[SimpleNamespace(entity_id=e) for e in entities],
        ),
        patch("custom_components.sunsynk.er.async_get"),
    )


@pytest.mark.asyncio
async def test_automations_and_scripts_are_always_allowed():
    await _async_ensure_control_allowed(_hass(None), _call(None), "SN1")
    await _async_ensure_control_allowed(_hass(None), SimpleNamespace(data={}), "SN1")


@pytest.mark.asyncio
async def test_unknown_user_is_refused():
    with pytest.raises(UnknownUser):
        await _async_ensure_control_allowed(_hass(None), _call(), "SN1")


@pytest.mark.asyncio
async def test_administrators_are_allowed():
    await _async_ensure_control_allowed(_hass(_user(admin=True)), _call(), "SN1")


@pytest.mark.asyncio
async def test_a_user_who_may_control_the_inverter_is_allowed():
    a, b, c = _registries()
    with a, b, c:
        await _async_ensure_control_allowed(
            _hass(_user(allowed={"switch.inv_x"})), _call(), "SN1"
        )


@pytest.mark.asyncio
async def test_a_read_only_user_is_refused():
    a, b, c = _registries()
    with a, b, c, pytest.raises(Unauthorized):
        await _async_ensure_control_allowed(_hass(_user()), _call(), "SN1")


@pytest.mark.asyncio
async def test_an_inverter_without_a_device_is_refused():
    a, b, c = _registries(device=False)
    with a, b, c, pytest.raises(Unauthorized):
        await _async_ensure_control_allowed(
            _hass(_user(allowed={"switch.inv_x"})), _call(), "SN1"
        )

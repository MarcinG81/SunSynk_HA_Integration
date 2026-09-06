"""Shared pytest fixtures for Sunsynk tests."""
from __future__ import annotations

from typing import Any, Self
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest


class FakeResponse:
    """Stands in for an aiohttp response used as `async with session.get(...) as resp`."""

    def __init__(self, json_data: Any, status: int = 200) -> None:
        self._json_data = json_data
        self.status = status

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise aiohttp.ClientResponseError(
                request_info=MagicMock(),
                history=(),
                status=self.status,
            )

    async def json(self) -> Any:
        return self._json_data


def fake_session(**method_return_values: Any) -> MagicMock:
    """A MagicMock aiohttp.ClientSession whose .get/.post return FakeResponse objects.

    Pass get=<FakeResponse-or-list-of-them> and/or post=<...>. A list is
    consumed one call at a time (via side_effect); a single value is
    returned for every call.
    """
    session = MagicMock()
    for method, value in method_return_values.items():
        mock_method = MagicMock()
        if isinstance(value, list):
            mock_method.side_effect = value
        else:
            mock_method.return_value = value
        setattr(session, method, mock_method)
    return session


@pytest.fixture
def mock_hass():
    """Return a minimal mock HomeAssistant instance."""
    hass = MagicMock()
    hass.states = MagicMock()
    hass.states.get = MagicMock(return_value=None)
    hass.services = MagicMock()
    return hass


@pytest.fixture
def mock_coordinator(mock_hass):
    """Return a mock SunsynkCoordinator."""
    coordinator = MagicMock()
    coordinator.serials = ["TEST123"]
    coordinator.data = {
        "TEST123": {
            "battery": {"soc": 50, "power": 0},
            "settings": {"chargeCurrent": 50, "dischargeCurrent": 50},
        }
    }
    coordinator.async_write_setting = AsyncMock()
    return coordinator

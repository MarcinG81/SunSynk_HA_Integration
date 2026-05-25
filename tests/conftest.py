"""Shared pytest fixtures for Sunsynk tests."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


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

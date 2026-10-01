"""Tests for Home Assistant repair flows."""
from unittest.mock import MagicMock

import pytest

from custom_components.sunsynk.repairs import async_create_fix_flow


@pytest.mark.asyncio
async def test_confirm_repair_flow_form_and_completion(mock_hass):
    flow = await async_create_fix_flow(mock_hass, "issue", None)
    flow.async_show_form = MagicMock(return_value={"type": "form"})
    flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})

    assert await flow.async_step_init() == {"type": "form"}
    assert await flow.async_step_init({}) == {"type": "create_entry"}

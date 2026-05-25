"""Repair flows for Sunsynk integration."""
from __future__ import annotations

from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult


class _ConfirmRepairFlow(RepairsFlow):
    async def async_step_init(self, user_input: dict | None = None) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data={})
        return self.async_show_form(step_id="init")


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict | None
) -> RepairsFlow:
    return _ConfirmRepairFlow()

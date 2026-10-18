"""Every Configure option is labelled, translated and documented correctly.

#38: a 1.9.x user looked for "Create and maintain Sunsynk dashboard", which
only 2.0 has, because the README listed it without saying so. These checks
keep the form, its translations and the README's options table in step.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.sunsynk.config_flow import SunsynkOptionsFlow

ROOT = Path(__file__).resolve().parent.parent
COMPONENT = ROOT / "custom_components" / "sunsynk"
LANGUAGES = sorted((COMPONENT / "translations").glob("*.json"))

# Options that only exist from 2.0; the README must mark them "(2.0)".
OPTIONS_NEW_IN_2_0 = {
    "create_dashboard",
    "admin_only_control",
    "max_export_power",
    "price_stale_check",
    "tariff_test_mode",
}


async def _form_fields() -> list[str]:
    entry = MagicMock(entry_id="entry", data={"serials": ["INV1"]}, options={})
    flow = SunsynkOptionsFlow(entry)
    flow.hass = SimpleNamespace(
        config=SimpleNamespace(latitude=51.0, longitude=-1.0),
        config_entries=MagicMock(),
        data={},
    )
    result = await flow.async_step_init()
    return [str(marker) for marker in result["data_schema"].schema]


def _labels(path: Path) -> dict[str, str]:
    return json.loads(path.read_text())["options"]["step"]["init"]["data"]


def _readme_options() -> dict[str, str]:
    """README "Options (Configure)" table: bold option name -> whole row."""
    text = (ROOT / "README.md").read_text()
    section = text.split("### Options (Configure)", 1)[1].split("\n### ", 1)[0]
    rows = [line for line in section.splitlines() if line.startswith("| **")]
    return {re.match(r"\| \*\*(.+?)\*\*", row).group(1): row for row in rows}


@pytest.mark.asyncio
async def test_every_form_field_has_a_label_in_every_language():
    fields = await _form_fields()
    for path in [COMPONENT / "strings.json", *LANGUAGES]:
        missing = [field for field in fields if field not in _labels(path)]
        assert not missing, f"{path.name} has no label for {missing}"


@pytest.mark.asyncio
async def test_no_language_labels_a_field_the_form_doesnt_have():
    fields = set(await _form_fields())
    for path in [COMPONENT / "strings.json", *LANGUAGES]:
        extra = set(_labels(path)) - fields
        assert not extra, f"{path.name} labels fields the form doesn't have: {extra}"


@pytest.mark.asyncio
async def test_every_option_in_the_readme_exists_in_the_form():
    labels = {label: key for key, label in _labels(COMPONENT / "strings.json").items()}
    fields = set(await _form_fields())
    for name in _readme_options():
        if name in ("Solar Forecast", "Tariff Manager"):
            continue  # groups of fields, documented in their own sections
        assert name in labels, f"README lists an option the form doesn't have: {name}"
        assert labels[name] in fields


def test_options_new_in_2_0_are_marked_in_the_readme():
    labels = _labels(COMPONENT / "strings.json")
    rows = _readme_options()
    for key in OPTIONS_NEW_IN_2_0:
        row = rows.get(labels[key])
        if row is not None:
            assert "*(2.0)*" in row, f"README doesn't mark {labels[key]!r} as 2.0"

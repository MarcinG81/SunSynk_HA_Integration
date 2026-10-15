"""Battery BMS SOC ignores placeholders (1.9.13; fix by David, @davidatjourney)."""

import pytest

from custom_components.sunsynk.const import _battery_bms_soc_value


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ({"bmsSoc": "87", "bmsVolt": "52.1"}, 87.0),
        ({"bmsSoc": "87"}, 87.0),
        ({"bmsSoc": "0", "bmsVolt": "0"}, None),
        ({"bmsSoc": "50", "bmsVolt": "nan"}, None),
        ({"bmsSoc": "150", "bmsVolt": "52"}, None),
        ({"bmsSoc": "bad"}, None),
        ({}, None),
    ],
)
def test_bms_soc_ignores_placeholders(data, expected):
    assert _battery_bms_soc_value(data) == expected

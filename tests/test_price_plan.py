"""Planning when to buy and sell from the prices ahead (#40)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from custom_components.sunsynk.price_plan import (
    Rate,
    by_day,
    in_slot,
    merge_rates,
    parse_rates,
    plan_day,
)

BST = timezone(timedelta(hours=1))
# The Octopus Agile Outgoing export rates of 2026-10-21 from #40, 16:00-20:00.
_EVENING = [0.2487, 0.2546, 0.2566, 0.2607, 0.2661, 0.2659, 0.1862, 0.1863]


def _octopus(day=21, prices=_EVENING, first_hour=16):
    start = datetime(2026, 10, day, first_hour, 0, tzinfo=BST)
    return {
        "rates": [
            {
                "start": (start + timedelta(minutes=30 * n)).isoformat(),
                "end": (start + timedelta(minutes=30 * (n + 1))).isoformat(),
                "value_inc_vat": price,
                "is_capped": False,
            }
            for n, price in enumerate(prices)
        ]
    }


def test_octopus_rates_are_read():
    rates = parse_rates(_octopus())

    assert len(rates) == 8
    assert rates[0].start == datetime(2026, 10, 21, 16, 0, tzinfo=BST)
    assert rates[0].hours == 0.5
    assert rates[4].price == 0.2661


def test_nord_pool_style_rates_are_read():
    start = datetime(2026, 10, 21, 0, 0, tzinfo=BST)
    item = lambda h, v: {  # noqa: E731
        "start": start + timedelta(hours=h),
        "end": start + timedelta(hours=h + 1),
        "value": v,
    }
    rates = parse_rates(
        {"raw_today": [item(0, 0.1), item(1, 0.2)], "raw_tomorrow": [item(24, 0.3)]}
    )

    assert [rate.price for rate in rates] == [0.1, 0.2, 0.3]
    assert rates[0].hours == 1


@pytest.mark.parametrize(
    "item",
    [
        "not a slot",
        {"start": "nonsense", "end": "2026-10-21T00:30:00+01:00", "value": 1},
        {"start": "2026-10-21T00:30:00+01:00", "end": "2026-10-21T00:00:00+01:00", "value": 1},
        {"start": "2026-10-21T00:00:00+01:00", "end": "2026-10-21T00:30:00+01:00", "value": "nan"},
        {"start": 5, "end": "2026-10-21T00:30:00+01:00", "value": 1},
        {"start": "2026-10-21T00:00:00+01:00", "end": "2026-10-21T00:30:00+01:00", "value": None},
    ],
)
def test_broken_slots_are_left_out(item):
    assert parse_rates({"raw_today": [item]}) == []


def test_a_time_without_a_zone_is_local():
    rates = parse_rates(
        {"raw_today": [{"start": "2026-10-21T00:00:00", "end": "2026-10-21T01:00:00", "value": 1}]}
    )

    assert rates[0].start.tzinfo is not None


def test_today_and_tomorrow_are_merged_in_order():
    today, tomorrow = parse_rates(_octopus(21)), parse_rates(_octopus(22))

    merged = merge_rates(tomorrow, today, today)

    assert len(merged) == 16
    assert merged == sorted(merged, key=lambda rate: rate.start)


def test_the_dearest_slots_to_sell_a_day_s_amount():
    """#40: 2.5 kWh at 5 kW is one hour, so the two dearest half hours."""
    rates = parse_rates(_octopus())
    before = datetime(2026, 10, 21, 12, 0, tzinfo=BST)

    plan = plan_day(rates, before, kwh=5, power_kw=5, dearest=True)

    assert [rate.price for rate in plan] == [0.2661, 0.2659]
    assert [rate.start.hour for rate in plan] == [18, 18]


def test_the_cheapest_slots_to_buy():
    rates = parse_rates(_octopus())

    plan = plan_day(
        rates, datetime(2026, 10, 21, 12, tzinfo=BST), kwh=2, power_kw=4, dearest=False
    )

    assert [rate.price for rate in plan] == [0.1862]


def test_slots_already_over_aren_t_planned():
    rates = parse_rates(_octopus())
    at_1830 = datetime(2026, 10, 21, 18, 30, tzinfo=BST)

    plan = plan_day(rates, at_1830, kwh=2.5, power_kw=5, dearest=True)

    assert [rate.price for rate in plan] == [0.2659]


def test_the_price_limit_is_kept_even_if_short_of_the_amount():
    rates = parse_rates(_octopus())

    plan = plan_day(
        rates,
        datetime(2026, 10, 21, 12, tzinfo=BST),
        kwh=100,
        power_kw=5,
        dearest=True,
        limit=0.26,
    )

    assert [rate.price for rate in plan] == [0.2607, 0.2661, 0.2659]


@pytest.mark.parametrize(("kwh", "power"), [(0, 5), (5, 0)])
def test_nothing_to_plan(kwh, power):
    assert plan_day(parse_rates(_octopus()), datetime(2026, 10, 21, tzinfo=BST), kwh, power, dearest=True) == []


def test_equal_prices_keep_the_earlier_slot():
    start = datetime(2026, 10, 21, 0, tzinfo=BST)
    rates = [
        Rate(start + timedelta(hours=h), start + timedelta(hours=h + 1), 0.2)
        for h in range(3)
    ]

    plan = plan_day(rates, start, kwh=1, power_kw=1, dearest=True)

    assert plan == [rates[0]]


def test_days_and_the_slot_in_force():
    rates = parse_rates(_octopus(21)) + parse_rates(_octopus(22))

    days = by_day(rates)

    assert sorted(day.day for day in days) == [21, 22]
    assert in_slot(rates, datetime(2026, 10, 21, 16, 45, tzinfo=BST)).price == 0.2546
    assert in_slot(rates, datetime(2026, 10, 21, 12, 0, tzinfo=BST)) is None

"""A parallel group recognised after setup merges its schedules (#21)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from custom_components.sunsynk import _adopt_stray_virtual_slot_entities
from custom_components.sunsynk.const import DOMAIN


class _FakeRegistry:
    def __init__(self, entities: dict[tuple[str, str], str]) -> None:
        self.entities = dict(entities)

    def async_get_entity_id(self, platform, domain, unique_id):
        assert domain == DOMAIN
        return self.entities.get((platform, unique_id))

    def async_remove(self, entity_id):
        self.entities = {k: v for k, v in self.entities.items() if v != entity_id}

    def async_update_entity(self, entity_id, *, new_unique_id):
        key = next(k for k, v in self.entities.items() if v == entity_id)
        del self.entities[key]
        self.entities[(key[0], new_unique_id)] = entity_id


_COORDINATOR = SimpleNamespace(
    serials=["SLAVE", "MASTER"],
    resolve_write_target=lambda serial: "MASTER",
)


def _adopt(entities, targets=frozenset({"MASTER"})):
    registry = _FakeRegistry(entities)
    with patch("custom_components.sunsynk.er.async_get", return_value=registry):
        _adopt_stray_virtual_slot_entities(object(), _COORDINATOR, set(targets))
    return registry.entities


def test_original_entity_ids_move_from_the_slave_to_the_master():
    entities = _adopt(
        {
            ("switch", "SLAVE_vslots_enabled"): "switch.home_virtual_slot_scheduler",
            ("sensor", "SLAVE_vslots_state"): "sensor.home_virtual_slot_scheduler",
            ("switch", "MASTER_vslots_enabled"): "switch.home_virtual_slot_scheduler_2",
            ("sensor", "MASTER_vslots_state"): "sensor.home_virtual_slot_scheduler_2",
        }
    )

    assert entities == {
        ("switch", "MASTER_vslots_enabled"): "switch.home_virtual_slot_scheduler",
        ("sensor", "MASTER_vslots_state"): "sensor.home_virtual_slot_scheduler",
    }


def test_master_keeps_its_original_ids_and_the_slave_copy_goes():
    entities = _adopt(
        {
            ("sensor", "SLAVE_vslots_state"): "sensor.home_virtual_slot_scheduler_2",
            ("sensor", "MASTER_vslots_state"): "sensor.home_virtual_slot_scheduler",
        }
    )

    assert entities == {
        ("sensor", "MASTER_vslots_state"): "sensor.home_virtual_slot_scheduler",
    }


def test_slave_only_entity_is_handed_to_the_master():
    entities = _adopt({("sensor", "SLAVE_vslots_state"): "sensor.home_vss"})

    assert entities == {("sensor", "MASTER_vslots_state"): "sensor.home_vss"}


def test_nothing_moves_without_a_master_scheduler():
    original = {("sensor", "SLAVE_vslots_state"): "sensor.home_vss"}

    assert _adopt(original, targets={"SLAVE"}) == original

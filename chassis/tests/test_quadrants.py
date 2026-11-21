"""Quadrant config loads, and the router reads it at startup."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from drayl.memory import InMemoryMemory
from drayl.quadrants import load_quadrant_map
from drayl.router import Router
from drayl.sandpit import InMemorySandPit

EXPECTED_IDS = [
    "numbers",
    "grammar_speech",
    "facts",
    "procedures",
    "personal",
    "misc",
]


def test_quadrant_config_loads(quadrant_map):
    assert quadrant_map.ids == EXPECTED_IDS
    assert "math" in quadrant_map.by_id["numbers"]["capability_tags"]
    assert "grammar" in quadrant_map.by_id["grammar_speech"]["capability_tags"]
    assert quadrant_map.tag_to_quadrants["facts"] == ["facts"]


def test_router_reads_map_at_startup(quadrant_map, sandpit, memory):
    router = Router(quadrant_map, sandpit, memory)
    assert router.quadrant_map is quadrant_map
    assert router.quadrant_map.ids == EXPECTED_IDS


def test_from_config_reads_default_file():
    router = Router.from_config(InMemorySandPit(), InMemoryMemory())
    assert router.quadrant_map.ids == EXPECTED_IDS


def test_missing_config_raises(tmp_path):
    missing = tmp_path / "no-such-map.json"
    with pytest.raises(FileNotFoundError):
        load_quadrant_map(missing)


def test_empty_quadrants_rejected(tmp_path):
    path = tmp_path / "empty.json"
    path.write_text(json.dumps({"quadrants": []}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_quadrant_map(path)


def test_explicit_tags_match_before_unmatched(quadrant_map):
    order, matched = quadrant_map.order_for(["personal", "how"])
    assert matched == ["personal", "how"]
    assert order[0] == "procedures" or order[0] == "personal"
    assert order.index("personal") < order.index("numbers")
    assert order.index("procedures") < order.index("misc")
    # Every quadrant still appears, so a retag is enough when boundaries change.
    assert set(order) == set(quadrant_map.ids)

"""Heat map writes JSONL and aggregates grain and quadrant counts."""

from __future__ import annotations

import json

from drayl_chat.heatmap import HeatMap


def test_log_appends_jsonl_and_aggregates(tmp_path):
    path = tmp_path / "heatmap.jsonl"
    heat = HeatMap(path)
    heat.log("hello", ["seed-hello"], ["misc"], block_id="b1")
    heat.log("two plus two", ["seed-two-plus-two", "seed-hello"], ["numbers", "misc"], block_id="b2")

    text = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(text) == 2
    first = json.loads(text[0])
    assert first["query"] == "hello"
    assert first["grains"] == ["seed-hello"]
    assert first["quadrants"] == ["misc"]
    assert "ts" in first

    agg = heat.aggregates()
    assert agg["queries"] == 2
    assert agg["grains"]["seed-hello"] == 2
    assert agg["grains"]["seed-two-plus-two"] == 1
    assert agg["quadrants"]["misc"] == 2
    assert agg["quadrants"]["numbers"] == 1


def test_empty_heatmap(tmp_path):
    heat = HeatMap(tmp_path / "missing.jsonl")
    assert heat.entries() == []
    assert heat.aggregates() == {"queries": 0, "grains": {}, "quadrants": {}}

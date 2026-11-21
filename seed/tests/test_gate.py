"""The gate promotes unique grains and stages near-duplicates."""

from __future__ import annotations

import json

from drayl.router import Block


def _read_jsonl(path):
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(line) for line in lines]


def test_gate_promotes_unique(gate, sandpit, tmp_path):
    decision = gate.offer("The sky is blue on a clear day", quadrant="facts")
    assert decision.accepted is True
    assert decision.reason == "promoted"
    assert decision.grain is not None
    assert sandpit.get(decision.grain.id) is not None
    staging = tmp_path / "near-misses.jsonl"
    assert not staging.exists() or staging.read_text(encoding="utf-8") == ""


def test_gate_stages_duplicates(gate, sandpit, tmp_path):
    first = gate.offer("The sky is blue on a clear day", quadrant="facts")
    assert first.accepted is True

    second = gate.offer("The sky is blue on a clear day", quadrant="facts")
    assert second.accepted is False
    assert second.reason == "near_duplicate"
    assert second.nearest_id == first.grain.id
    assert second.nearest_cosine is not None and second.nearest_cosine >= 0.92

    staging = tmp_path / "near-misses.jsonl"
    records = _read_jsonl(staging)
    assert len(records) == 1
    assert records[0]["reason"] == "near_duplicate"
    assert records[0]["nearest_id"] == first.grain.id
    assert records[0]["text"] == "The sky is blue on a clear day"
    # The duplicate must not have been added again.
    assert len(sandpit.all_grains()) == 1


def test_pending_retrieval_check_does_not_block_unique(gate):
    decision = gate.offer("A unique fact about Queensland weather", quadrant="facts")
    assert decision.accepted is True
    assert "improves_later_retrieval" in decision.pending_checks


def test_write_back_goes_through_the_router(router, sandpit):
    decision = router.write_back("Mate runs offline on the laptop", quadrant="facts")
    assert decision.accepted is True
    assert sandpit.get(decision.grain.id) is not None

    # A later dispatch can find the promoted grain.
    result = router.dispatch(Block(text="does Mate run offline", tags=("facts",)))
    assert any(grain.id == decision.grain.id for grain in result.grains)

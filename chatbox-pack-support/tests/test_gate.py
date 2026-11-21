"""Compliance gate: empty, length limits, near-duplicates. Never auto-promotes."""

from __future__ import annotations

from drayl_chat.gate import ComplianceGate
from drayl_chat.sandpit import make_grain


def test_empty_and_whitespace_fail():
    gate = ComplianceGate()
    assert gate.check("", []).accepted is False
    assert gate.check("   ", []).reason == "empty"


def test_too_short_and_too_long():
    gate = ComplianceGate(min_len=8, max_len=20)
    assert gate.check("short", []).reason == "too_short"
    assert gate.check("x" * 21, []).reason == "too_long"


def test_unique_grain_passes():
    gate = ComplianceGate()
    existing = [make_grain("Two plus two is four.", "numbers", "g1")]
    decision = gate.check("A sentence starts with a capital letter.", existing)
    assert decision.accepted is True
    assert decision.reason == "pass"


def test_near_duplicate_fails():
    gate = ComplianceGate(cosine_threshold=0.92)
    existing = [make_grain("Two plus two is four.", "numbers", "g1")]
    decision = gate.check("Two plus two is four.", existing)
    assert decision.accepted is False
    assert decision.reason == "near_duplicate"
    assert decision.nearest_id == "g1"
    assert decision.nearest_cosine is not None
    assert decision.nearest_cosine >= 0.92


def test_gate_does_not_mutate_sandpit(sandpit):
    before = {grain.id for grain in sandpit.all_grains()}
    gate = ComplianceGate()
    gate.check("A completely new fact about zebras wearing hats at noon.", sandpit.all_grains())
    after = {grain.id for grain in sandpit.all_grains()}
    assert before == after

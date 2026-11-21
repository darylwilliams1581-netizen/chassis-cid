"""Write-back stages files and only promotes when asked and the gate passes."""

from __future__ import annotations

from drayl_chat.gate import ComplianceGate
from drayl_chat.writeback import WriteBack


def test_stage_writes_json_and_does_not_promote(data_dir, sandpit):
    before = {grain.id for grain in sandpit.all_grains()}
    wb = WriteBack(data_dir / "staging", sandpit, ComplianceGate())
    path = wb.stage(
        text="Q: Unique zebra question\nA: A made-up zebra fact for staging.",
        quadrant="facts",
        tags=["fact"],
        source_query="Unique zebra question",
        reply_text="A made-up zebra fact for staging.",
        candidate_id="cand-zebra-1",
    )
    assert path.is_file()
    after = {grain.id for grain in sandpit.all_grains()}
    assert before == after
    listed = wb.list_candidates()
    assert any(item["id"] == "cand-zebra-1" for item in listed)


def test_promote_passing_only_moves_accepted(data_dir, sandpit):
    wb = WriteBack(data_dir / "staging", sandpit, ComplianceGate())
    wb.stage(
        text="Two plus two is four.",
        quadrant="numbers",
        source_query="dupe",
        candidate_id="cand-dupe",
    )
    wb.stage(
        text="A brand new fact about teal lanterns in a quiet harbour.",
        quadrant="facts",
        source_query="lanterns",
        candidate_id="cand-new",
    )
    results = {item["id"]: item for item in wb.promote_passing()}
    assert results["cand-dupe"]["promoted"] is False
    assert results["cand-dupe"]["reason"] == "near_duplicate"
    assert results["cand-new"]["promoted"] is True
    assert sandpit.get("cand-new") is not None
    assert (data_dir / "staging" / "cand-dupe.json").is_file()
    assert not (data_dir / "staging" / "cand-new.json").exists()

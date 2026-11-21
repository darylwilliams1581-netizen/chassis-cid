"""Merge keeps raw sand grains and memory context, and boosts what helped."""

from __future__ import annotations

from drayl_chat.memory import MemoryResult, MemoryTrace
from drayl_chat.merge import HELP_BOOST, merge
from drayl_chat.sandpit import Grain, SandHit, SandResult


def _hit(grain_id: str, text: str, quadrant: str, score: float) -> SandHit:
    grain = Grain(
        id=grain_id,
        text=text,
        quadrant=quadrant,
        embedding=(1.0, 0.0),
        tier="warm",
    )
    return SandHit(grain=grain, score=score, quadrant=quadrant)


def test_merge_contains_both_arms(router, sandpit, memory, make_block):
    memory.remember(
        "what is two plus two",
        "last time the numbers grain helped",
        helped_grain_ids=["seed-two-plus-two"],
        helped_quadrants=["numbers"],
    )
    result = router.dispatch(make_block("what is two plus two"))
    grain_ids = [hit.grain_id for hit in result.hits]
    assert "seed-two-plus-two" in grain_ids
    assert any("four" in hit.text.lower() for hit in result.hits)
    assert "numbers grain helped" in result.memory.context
    assert result.memory.traces
    assert result.memory.source in {"fast", "slow", "both"}


def test_merge_boosts_grains_that_helped_before(make_block):
    sand = SandResult(
        hits=[
            _hit("g-a", "alpha grain", "facts", 0.50),
            _hit("g-b", "beta grain", "facts", 0.55),
        ],
        quadrant_order=["facts"],
        accessed_ids=["g-a", "g-b"],
        accessed_quadrants=["facts"],
    )
    memory = MemoryResult(
        context="alpha helped last time",
        traces=[
            MemoryTrace(
                id="t1",
                question="tell me about alpha",
                what_mattered="alpha helped last time",
                helped_grain_ids=["g-a"],
                helped_quadrants=["facts"],
                timestamp=0.0,
                score=0.8,
            )
        ],
        source="fast",
        helped_grain_ids=["g-a"],
        helped_quadrants=["facts"],
    )
    result = merge(make_block("tell me about alpha"), sand, memory, ["facts"], ["fact"])
    by_id = {hit.grain_id: hit for hit in result.hits}
    assert by_id["g-a"].boosted is True
    assert by_id["g-b"].boosted is False
    assert by_id["g-a"].score == min(1.0, 0.50 + HELP_BOOST)
    # Boosted alpha now outranks the slightly higher raw score of beta.
    assert result.hits[0].grain_id == "g-a"
    assert result.memory_boosted == ["g-a"]
    assert "alpha helped last time" in result.notes


def test_merge_deduplicates_grain_ids(make_block):
    sand = SandResult(
        hits=[
            _hit("g-a", "same grain", "misc", 0.40),
            _hit("g-a", "same grain", "misc", 0.70),
        ],
        quadrant_order=["misc"],
        accessed_ids=["g-a"],
        accessed_quadrants=["misc"],
    )
    memory = MemoryResult(context="", traces=[], source="none")
    result = merge(make_block("hello"), sand, memory, ["misc"])
    assert len(result.hits) == 1
    assert result.hits[0].score == 0.70


def test_merge_empty_both_arms(make_block):
    sand = SandResult(hits=[], quadrant_order=["misc"], accessed_ids=[], accessed_quadrants=[])
    memory = MemoryResult(context="", traces=[], source="none")
    result = merge(make_block("zzzz unusual"), sand, memory, ["misc"])
    assert result.hits == []
    assert result.notes == []
    assert result.memory_boosted == []

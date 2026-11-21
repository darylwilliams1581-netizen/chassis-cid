"""The merge keeps raw sand grains and memory context, not just one arm."""

from __future__ import annotations

from drayl.router import Block


def test_merge_contains_both_arms(router, sandpit, memory):
    grain = sandpit.add(
        "Paris is the capital of France",
        quadrant="facts",
        grain_id="grain-paris",
    )
    memory.remember(
        "what is the capital of France",
        "last time, knowing the user was travelling to Paris mattered",
        helped_quadrants=["facts"],
    )

    result = router.dispatch(Block(text="what is the capital of France", tags=("facts",)))

    grain_ids = [item.id for item in result.grains]
    assert "grain-paris" in grain_ids
    assert result.grains[0].text == grain.text
    assert "travelling to Paris" in result.memory.context
    assert result.memory.traces
    assert result.memory.source in {"fast", "slow", "both"}


def test_heat_updates_on_sand_access(router, sandpit):
    grain = sandpit.add("two plus two is four", quadrant="numbers", grain_id="grain-sum")
    assert grain.hits == 0
    assert grain.last_access is None

    router.dispatch(Block(text="what is two plus two", tags=("numbers",)))

    stored = sandpit.get("grain-sum")
    assert stored is not None
    assert stored.hits == 1
    assert stored.last_access is not None

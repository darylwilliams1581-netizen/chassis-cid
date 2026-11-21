"""Memory can change which quadrant the sand pit searches first."""

from __future__ import annotations

from drayl.router import Block


def test_memory_bias_changes_quadrant_order(router, quadrant_map):
    default_order, _matched = quadrant_map.order_for(("facts",))
    assert default_order[0] == "facts"

    router.memory.remember(
        "capital of France",
        "the user lives in Paris, so personal context mattered",
        helped_quadrants=["personal", "facts"],
    )
    result = router.dispatch(Block(text="capital of France", tags=("facts",)))

    assert result.quadrant_order[0] == "personal"
    assert result.quadrant_order[1] == "facts"
    assert result.quadrant_order != default_order
    # The rest of the map is still present after the biased prefix.
    assert set(result.quadrant_order) == set(quadrant_map.ids)


def test_no_memory_keeps_default_order(router, quadrant_map):
    default_order, _matched = quadrant_map.order_for(("numbers",))
    result = router.dispatch(Block(text="add two plus two", tags=("numbers",)))
    assert result.quadrant_order == default_order
    assert result.quadrant_order[0] == "numbers"
    assert result.memory.source == "none"

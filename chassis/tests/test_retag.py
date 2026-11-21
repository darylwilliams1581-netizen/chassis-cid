"""Changing quadrant boundaries retags grains and does not rebuild the index."""

from __future__ import annotations

from drayl.embeddings import hashed_bow_embedding
from drayl.router import Block


def test_retag_does_not_rebuild_index(sandpit):
    grain = sandpit.add("how to boil an egg", quadrant="misc", grain_id="grain-egg")
    assert sandpit.rebuild_count == 0

    sandpit.retag(grain.id, "procedures")

    assert sandpit.rebuild_count == 0
    assert sandpit.get("grain-egg").quadrant == "procedures"
    # The same grain object is still in the pit (no index rebuild / rewrite).
    assert sandpit.get("grain-egg") is grain


def test_retag_moves_search_to_the_new_quadrant(router, sandpit):
    grain = sandpit.add(
        "how to boil an egg in water",
        quadrant="misc",
        grain_id="grain-egg",
    )
    query = hashed_bow_embedding("how to boil an egg in water")

    before = sandpit.search(query, quadrant_order=["procedures"], k=5)
    assert grain.id not in {item.id for item in before}

    sandpit.retag(grain.id, "procedures")
    assert sandpit.rebuild_count == 0

    after = sandpit.search(query, quadrant_order=["procedures"], k=5)
    assert grain.id in {item.id for item in after}

    # And a router dispatch that prefers procedures now sees it first.
    result = router.dispatch(Block(text="how to boil an egg", tags=("procedures",)))
    assert result.grains
    assert result.grains[0].id == "grain-egg"
    assert result.grains[0].quadrant == "procedures"


def test_retag_unknown_grain_raises(sandpit):
    import pytest

    with pytest.raises(KeyError):
        sandpit.retag("does-not-exist", "facts")

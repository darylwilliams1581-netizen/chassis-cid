"""Trusted grains win ties and near-ties in the merge."""

from __future__ import annotations

import math

from drayl.merge import prefer_trusted
from drayl.router import Block
from drayl.sandpit import Grain


def _grain(gid, embedding, trusted, quadrant="facts") -> Grain:
    return Grain(
        id=gid,
        text=gid,
        embedding=embedding,
        quadrant=quadrant,
        trusted=trusted,
    )


def test_merge_trusted_wins_an_exact_tie(router, sandpit):
    text = "Paris is the capital of France"
    sandpit.add(text, "facts", grain_id="untrusted-paris", trusted=False)
    sandpit.add(text, "facts", grain_id="trusted-paris", trusted=True)
    result = router.dispatch(Block(text=text, tags=("facts",)))
    assert result.grains[0].id == "trusted-paris"
    assert result.grains[0].trusted is True
    assert {g.id for g in result.grains} >= {"trusted-paris", "untrusted-paris"}


def test_merge_trusted_wins_within_the_margin():
    # Query is exactly the untrusted vector (score 1.0). Trusted is 0.97 away,
    # which is inside the default 0.05 margin, so trusted still ranks first.
    query = (1.0, 0.0)
    untrusted_vec = (1.0, 0.0)
    trusted_score = 0.97
    trusted_vec = (trusted_score, math.sqrt(1.0 - trusted_score ** 2))
    trusted = _grain("trusted", trusted_vec, True)
    untrusted = _grain("untrusted", untrusted_vec, False)
    ranked = prefer_trusted(
        [untrusted, trusted], query, quadrant_order=["facts"], margin=0.05
    )
    assert [g.id for g in ranked] == ["trusted", "untrusted"]


def test_merge_untrusted_wins_when_clearly_better():
    query = (1.0, 0.0)
    untrusted = _grain("untrusted", (1.0, 0.0), False)
    trusted = _grain("trusted", (0.80, 0.60), True)
    ranked = prefer_trusted(
        [untrusted, trusted], query, quadrant_order=["facts"], margin=0.05
    )
    # Untrusted cosine is 1.0, trusted is 0.80; 0.85 still loses.
    assert [g.id for g in ranked] == ["untrusted", "trusted"]

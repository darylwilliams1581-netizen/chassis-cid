"""Merge helpers. Trusted grains win ties and near-ties against untrusted ones."""

from __future__ import annotations

from typing import Iterable

from drayl.embeddings import cosine
from drayl.sandpit import Grain

# If a trusted grain is within this cosine of an untrusted one, the
# trusted grain is ranked first. A clearly better untrusted score still wins.
DEFAULT_TRUST_MARGIN = 0.05


def prefer_trusted(
    grains: Iterable[Grain],
    query_vec: tuple[float, ...],
    quadrant_order: Iterable[str],
    margin: float = DEFAULT_TRUST_MARGIN,
) -> list[Grain]:
    """Re-rank grains within each quadrant, leaving quadrant order intact.

    ranking_score = cosine + margin if the grain is trusted, else cosine.
    """
    items = list(grains)
    if not items:
        return []
    order = list(quadrant_order)
    rank = {qid: index for index, qid in enumerate(order)}
    fallback = len(order)

    def sort_key(grain: Grain) -> tuple[int, float, float, int]:
        score = cosine(query_vec, grain.embedding)
        boosted = score + (margin if grain.trusted else 0.0)
        return (
            rank.get(grain.quadrant, fallback),
            -boosted,
            -score,
            0 if grain.trusted else 1,
        )

    return sorted(items, key=sort_key)

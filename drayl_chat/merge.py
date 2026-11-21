"""Merge sand-pit hits with memory context.

Sand grains are raw content. Memory is context about what mattered last
time a similar question was asked. Memory can boost grains that helped
before, and those boosted grains are marked so the reply can say so.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from drayl_chat.encoder import Block
from drayl_chat.memory import MemoryResult
from drayl_chat.sandpit import SandHit, SandResult

HELP_BOOST = 0.15


@dataclass
class MergedHit:
    grain_id: str
    text: str
    quadrant: str
    score: float
    boosted: bool
    tier: str
    tags: list[str] = field(default_factory=list)
    symbol_ids: list[int] = field(default_factory=list)


@dataclass
class MergedResult:
    hits: list[MergedHit]
    memory: MemoryResult
    quadrant_order: list[str]
    matched_tags: list[str] = field(default_factory=list)
    memory_boosted: list[str] = field(default_factory=list)
    used_quadrants: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def merge(
    block: Block,
    sand: SandResult,
    memory: MemoryResult,
    quadrant_order: list[str],
    matched_tags: list[str] | None = None,
    boost: float = HELP_BOOST,
) -> MergedResult:
    """Combine both arms into one ranked result.

    - Duplicate grain ids appear once (highest score wins).
    - Grains that memory says helped last time get a score boost.
    - Memory notes (context / traces) are carried through unchanged.
    """
    helped = set(memory.helped_grain_ids)
    for trace in memory.traces:
        helped.update(trace.helped_grain_ids)

    by_id: dict[str, MergedHit] = {}
    for hit in sand.hits:
        boosted = hit.grain.id in helped
        score = min(1.0, hit.score + boost) if boosted else hit.score
        existing = by_id.get(hit.grain.id)
        if existing is None or score > existing.score:
            by_id[hit.grain.id] = MergedHit(
                grain_id=hit.grain.id,
                text=hit.grain.text,
                quadrant=hit.grain.quadrant,
                score=score,
                boosted=boosted,
                tier=hit.grain.tier,
                tags=list(hit.grain.tags),
                symbol_ids=list(hit.grain.symbol_ids),
            )

    hits = sorted(by_id.values(), key=lambda item: item.score, reverse=True)
    boosted_ids = [hit.grain_id for hit in hits if hit.boosted]
    used_quadrants: list[str] = []
    for hit in hits:
        if hit.quadrant not in used_quadrants:
            used_quadrants.append(hit.quadrant)
    for qid in memory.helped_quadrants:
        if qid not in used_quadrants:
            used_quadrants.append(qid)

    notes: list[str] = []
    if memory.context:
        notes.append(memory.context)
    for trace in memory.traces:
        if trace.what_mattered and trace.what_mattered not in notes:
            notes.append(trace.what_mattered)

    # block is accepted so callers can pass the request through; unused today
    # other than making the function signature match the pipeline.
    _ = block

    return MergedResult(
        hits=hits,
        memory=memory,
        quadrant_order=list(quadrant_order),
        matched_tags=list(matched_tags or []),
        memory_boosted=boosted_ids,
        used_quadrants=used_quadrants,
        notes=notes,
    )

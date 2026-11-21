"""Compliance gate for the write-back path.

Every decoder-emitted answer will later be chunked, embedded, and offered
here as a candidate grain. This scaffold implements the unique-coverage /
near-duplicate check. 'Improves later retrieval' is recorded as a pending
check and does not block promotion yet.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from drayl.embeddings import cosine, hashed_bow_embedding
from drayl.sandpit import Grain, SandPit, make_grain

# Pending checks that are part of the spec but not implemented in P36.
PENDING_CHECKS = ("improves_later_retrieval",)


@dataclass
class GateDecision:
    accepted: bool
    reason: str
    grain: Grain | None = None
    staged_path: Path | None = None
    nearest_id: str | None = None
    nearest_cosine: float | None = None
    pending_checks: tuple[str, ...] = PENDING_CHECKS


class ComplianceGate:
    """Promote unique grains; stage near-duplicates into a JSONL corpus."""

    def __init__(
        self,
        sandpit: SandPit,
        staging_path: str | Path,
        cosine_threshold: float = 0.92,
    ):
        self.sandpit = sandpit
        self.staging_path = Path(staging_path)
        self.cosine_threshold = cosine_threshold
        self.staging_path.parent.mkdir(parents=True, exist_ok=True)

    def offer(
        self,
        text: str,
        quadrant: str,
        grain_id: str | None = None,
    ) -> GateDecision:
        candidate = make_grain(text, quadrant, grain_id=grain_id)
        nearest_id, nearest_score = self._nearest(candidate.embedding)
        if nearest_id is not None and nearest_score >= self.cosine_threshold:
            path = self._stage(
                {
                    "reason": "near_duplicate",
                    "text": text,
                    "quadrant": quadrant,
                    "candidate_id": candidate.id,
                    "nearest_id": nearest_id,
                    "cosine": nearest_score,
                    "pending_checks": list(PENDING_CHECKS),
                }
            )
            return GateDecision(
                accepted=False,
                reason="near_duplicate",
                grain=candidate,
                staged_path=path,
                nearest_id=nearest_id,
                nearest_cosine=nearest_score,
            )
        # Unique coverage passed. The 'improves later retrieval' check is
        # still a stub, so it is recorded as pending and does not block.
        self.sandpit.promote(candidate)
        return GateDecision(
            accepted=True,
            reason="promoted",
            grain=candidate,
            nearest_id=nearest_id,
            nearest_cosine=nearest_score,
        )

    def _nearest(self, embedding: tuple[float, ...]) -> tuple[str | None, float]:
        best_id: str | None = None
        best_score = -1.0
        for grain in self.sandpit.all_grains():
            score = cosine(embedding, grain.embedding)
            if score > best_score:
                best_id = grain.id
                best_score = score
        if best_id is None:
            return None, 0.0
        return best_id, best_score

    def _stage(self, record: dict) -> Path:
        with self.staging_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
        return self.staging_path

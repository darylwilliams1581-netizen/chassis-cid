"""Compliance gate for the write-back path.

Every decoder-emitted answer will later be chunked, embedded, and offered
here as a candidate grain. Unique untrusted grains still have a pending
"improves later retrieval" proving period. Trusted school-mode grains
skip that proving period because the source itself is the proof.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from drayl.embeddings import cosine
from drayl.sandpit import Grain, SandPit, make_grain

# Pending checks that still apply to untrusted grains.
PENDING_CHECKS = ("improves_later_retrieval",)


def _normalised(text: str) -> str:
    return " ".join(text.split()).casefold()


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
        trusted: bool = False,
        source_name: str | None = None,
        source_sha256: str | None = None,
    ) -> GateDecision:
        if not text or not text.strip():
            return GateDecision(
                accepted=False,
                reason="empty",
                grain=None,
                pending_checks=() if trusted else PENDING_CHECKS,
            )

        candidate = make_grain(
            text,
            quadrant,
            grain_id=grain_id,
            trusted=trusted,
            source_name=source_name,
            source_sha256=source_sha256,
        )

        if trusted:
            return self._offer_trusted(candidate, text)

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
                    "trusted": False,
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

    def _offer_trusted(self, candidate: Grain, text: str) -> GateDecision:
        # Exact duplicate of an existing trusted grain is still rejected.
        # Near-duplicates of untrusted grains do not block: sleep will
        # override those untrusted grains later.
        for grain in self.sandpit.query(trusted=True, include_superseded=False):
            if _normalised(grain.text) == _normalised(text):
                path = self._stage(
                    {
                        "reason": "exact-duplicate",
                        "text": text,
                        "quadrant": candidate.quadrant,
                        "candidate_id": candidate.id,
                        "nearest_id": grain.id,
                        "cosine": 1.0,
                        "trusted": True,
                        "pending_checks": [],
                    }
                )
                return GateDecision(
                    accepted=False,
                    reason="exact-duplicate",
                    grain=candidate,
                    staged_path=path,
                    nearest_id=grain.id,
                    nearest_cosine=1.0,
                    pending_checks=(),
                )
        nearest_id, nearest_score = self._nearest(candidate.embedding)
        self.sandpit.promote(candidate)
        return GateDecision(
            accepted=True,
            reason="trusted-source",
            grain=candidate,
            nearest_id=nearest_id,
            nearest_cosine=nearest_score,
            pending_checks=(),  # proving period skipped; the source is the proof
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

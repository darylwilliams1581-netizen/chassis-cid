"""Compliance gate for candidate grains.

Candidates are checked, never auto-promoted. A passing result only
means "this may be promoted if you say so."
"""

from __future__ import annotations

from dataclasses import dataclass

from drayl_chat.embeddings import cosine, hashed_bow_embedding
from drayl_chat.sandpit import Grain

MIN_LEN = 8
MAX_LEN = 2000
DEFAULT_DUP_THRESHOLD = 0.92


@dataclass
class GateDecision:
    accepted: bool
    reason: str
    nearest_id: str | None = None
    nearest_cosine: float | None = None


class ComplianceGate:
    """Check a candidate: non-empty, length limits, not a near-duplicate."""

    def __init__(
        self,
        min_len: int = MIN_LEN,
        max_len: int = MAX_LEN,
        cosine_threshold: float = DEFAULT_DUP_THRESHOLD,
    ):
        self.min_len = min_len
        self.max_len = max_len
        self.cosine_threshold = cosine_threshold

    def check(self, text: str, existing: list[Grain]) -> GateDecision:
        cleaned = (text or "").strip()
        if not cleaned:
            return GateDecision(accepted=False, reason="empty")
        if len(cleaned) < self.min_len:
            return GateDecision(accepted=False, reason="too_short")
        if len(cleaned) > self.max_len:
            return GateDecision(accepted=False, reason="too_long")

        embedding = hashed_bow_embedding(cleaned)
        nearest_id: str | None = None
        nearest_score = -1.0
        for grain in existing:
            score = cosine(embedding, grain.embedding)
            if score > nearest_score:
                nearest_id = grain.id
                nearest_score = score
        if nearest_id is not None and nearest_score >= self.cosine_threshold:
            return GateDecision(
                accepted=False,
                reason="near_duplicate",
                nearest_id=nearest_id,
                nearest_cosine=nearest_score,
            )
        return GateDecision(
            accepted=True,
            reason="pass",
            nearest_id=nearest_id,
            nearest_cosine=None if nearest_score < 0 else nearest_score,
        )

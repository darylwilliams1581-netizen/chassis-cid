"""Minimal sleep-mode reconciliation for school mode (P38).

Within a quadrant, an untrusted grain that is a near-duplicate of a
trusted grain is overridden: it is marked superseded_by=<trusted id>
and moved to staging. It is never silently deleted. Untrusted grains
never override trusted ones. Conflicts across quadrants are left alone.

The broader sleep plan (hot/cold tiers, memory-protected cold grains,
poison-grain catching) is still only sketched, not built.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from drayl.embeddings import cosine
from drayl.sandpit import InMemorySandPit


@dataclass
class SleepChange:
    grain_id: str
    superseded_by: str
    quadrant: str
    cosine: float
    text: str


@dataclass
class SleepReport:
    overridden: list[SleepChange] = field(default_factory=list)
    kept: int = 0
    staging_path: Path | None = None

    def as_dict(self) -> dict:
        return {
            "overridden": [change.__dict__ for change in self.overridden],
            "kept": self.kept,
            "staging_path": str(self.staging_path) if self.staging_path else None,
        }


class SleepMode:
    """School-mode reconciliation pass over one sand pit."""

    def __init__(
        self,
        sandpit: InMemorySandPit,
        staging_path: str | Path,
        cosine_threshold: float = 0.92,
    ):
        self.sandpit = sandpit
        self.staging_path = Path(staging_path)
        self.cosine_threshold = cosine_threshold
        self.staging_path.parent.mkdir(parents=True, exist_ok=True)

    def run(self) -> SleepReport:
        report = SleepReport(staging_path=self.staging_path)
        # Group active grains by quadrant. Only compare inside a quadrant.
        by_quadrant: dict[str, list] = {}
        for grain in self.sandpit.all_grains():
            by_quadrant.setdefault(grain.quadrant, []).append(grain)

        to_override: list[tuple[str, str, str, float, str]] = []
        for quadrant, grains in by_quadrant.items():
            trusted = [grain for grain in grains if grain.trusted]
            untrusted = [grain for grain in grains if not grain.trusted]
            for untrusted_grain in untrusted:
                best_trusted = None
                best_score = -1.0
                for trusted_grain in trusted:
                    score = cosine(untrusted_grain.embedding, trusted_grain.embedding)
                    if score > best_score:
                        best_trusted = trusted_grain
                        best_score = score
                if best_trusted is not None and best_score >= self.cosine_threshold:
                    to_override.append(
                        (
                            untrusted_grain.id,
                            best_trusted.id,
                            quadrant,
                            best_score,
                            untrusted_grain.text,
                        )
                    )

        for grain_id, trusted_id, quadrant, score, text in to_override:
            # Re-read in case the grain was already moved.
            if self.sandpit.get(grain_id) is None or grain_id not in {
                g.id for g in self.sandpit.all_grains()
            }:
                continue
            demoted = self.sandpit.supersede(grain_id, trusted_id)
            self._stage(demoted, score)
            report.overridden.append(
                SleepChange(
                    grain_id=demoted.id,
                    superseded_by=trusted_id,
                    quadrant=quadrant,
                    cosine=score,
                    text=text,
                )
            )

        report.kept = len(self.sandpit.all_grains())
        return report

    def _stage(self, grain, score: float) -> None:
        record = grain.record()
        record["reason"] = "superseded-by-trusted"
        record["cosine"] = score
        # Keep the full grain body so nothing is silently deleted.
        with self.staging_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")

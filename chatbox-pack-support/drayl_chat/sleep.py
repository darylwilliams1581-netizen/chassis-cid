"""Sleep mode: a manual reconciliation pass over the sand pit.

Promotes hot grains (high access counts) and demotes unused ones to
cold. Protected grains are not demoted, even if they are unused.
Sleep does not run unless at least one query has been logged, so a
fresh install is not emptied on the first press.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from drayl_chat.heatmap import HeatMap
from drayl_chat.sandpit import SandPit

HOT_THRESHOLD = 3


@dataclass
class SleepReport:
    ran: bool
    message: str
    queries: int
    promoted: list[str] = field(default_factory=list)
    demoted: list[str] = field(default_factory=list)
    protected_kept: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    grain_counts: dict[str, int] = field(default_factory=dict)
    quadrant_counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "ran": self.ran,
            "message": self.message,
            "queries": self.queries,
            "promoted": list(self.promoted),
            "demoted": list(self.demoted),
            "protected_kept": list(self.protected_kept),
            "unchanged": list(self.unchanged),
            "grain_counts": dict(self.grain_counts),
            "quadrant_counts": dict(self.quadrant_counts),
        }


def run_sleep(
    sandpit: SandPit,
    heatmap: HeatMap,
    hot_threshold: int = HOT_THRESHOLD,
) -> SleepReport:
    aggregates = heatmap.aggregates()
    queries = int(aggregates["queries"])
    grain_counts = {key: int(value) for key, value in aggregates["grains"].items()}
    if queries < 1:
        return SleepReport(
            ran=False,
            message="No queries have been logged yet, so sleep had nothing to reconcile.",
            queries=0,
            grain_counts=grain_counts,
            quadrant_counts=dict(aggregates["quadrants"]),
        )

    promoted: list[str] = []
    demoted: list[str] = []
    protected_kept: list[str] = []
    unchanged: list[str] = []

    for grain in sandpit.all_grains():
        count = grain_counts.get(grain.id, 0)
        # Keep the live hit counter in step with the heat map as well.
        if count > grain.hits:
            grain.hits = count
        if count >= hot_threshold:
            target = "hot"
        elif count == 0:
            target = "cold"
        else:
            target = "warm"

        if target == "cold" and grain.protected:
            if grain.tier != "warm":
                grain.tier = "warm"
            protected_kept.append(grain.id)
            continue

        if grain.tier == target:
            unchanged.append(grain.id)
            continue

        previous = grain.tier
        grain.tier = target
        if target == "hot" and previous != "hot":
            promoted.append(grain.id)
        elif target == "cold" and previous != "cold":
            demoted.append(grain.id)
        elif target == "warm" and previous == "cold":
            promoted.append(grain.id)
        elif target == "warm" and previous == "hot":
            demoted.append(grain.id)
        else:
            unchanged.append(grain.id)

    sandpit.save_state()
    parts = [
        f"Sleep finished after {queries} logged quer"
        + ("y" if queries == 1 else "ies")
        + "."
    ]
    parts.append(f"Promoted to a hotter tier: {len(promoted)}.")
    parts.append(f"Demoted to a colder tier: {len(demoted)}.")
    if protected_kept:
        parts.append(f"Protected grains left in place: {len(protected_kept)}.")
    return SleepReport(
        ran=True,
        message=" ".join(parts),
        queries=queries,
        promoted=promoted,
        demoted=demoted,
        protected_kept=protected_kept,
        unchanged=unchanged,
        grain_counts=grain_counts,
        quadrant_counts={key: int(value) for key, value in aggregates["quadrants"].items()},
    )

"""Sand pit stub: in-memory vector store of grains, partitioned by quadrant."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from drayl_chat.embeddings import cosine, hashed_bow_embedding, symbols_for, tokenize

TIERS = ("hot", "warm", "cold")


@dataclass
class Grain:
    """One piece of stored content (a sand grain)."""

    id: str
    text: str
    quadrant: str
    tags: list[str] = field(default_factory=list)
    embedding: tuple[float, ...] = field(default_factory=tuple, repr=False)
    symbol_ids: list[int] = field(default_factory=list)
    tier: str = "warm"
    protected: bool = False
    hits: int = 0
    last_access: float | None = None
    helped_count: int = 0
    trusted: bool = False
    source_name: str | None = None
    source_sha256: str | None = None

    def source(self) -> dict | None:
        if self.source_name or self.source_sha256:
            return {"name": self.source_name, "sha256": self.source_sha256}
        return None

    def to_public_dict(self) -> dict:
        return {
            "id": self.id,
            "text": self.text,
            "quadrant": self.quadrant,
            "tags": list(self.tags),
            "tier": self.tier,
            "protected": self.protected,
            "hits": self.hits,
            "last_access": self.last_access,
            "helped_count": self.helped_count,
            "symbol_ids": list(self.symbol_ids),
            "trusted": self.trusted,
            "source": self.source(),
        }


def _source_fields(entry_or_name, sha=None) -> tuple[str | None, str | None]:
    if isinstance(entry_or_name, dict):
        return entry_or_name.get("name"), entry_or_name.get("sha256")
    return entry_or_name, sha


def make_grain(
    text: str,
    quadrant: str,
    grain_id: str,
    tags: Iterable[str] | None = None,
    tier: str = "warm",
    protected: bool = False,
    trusted: bool = False,
    source_name: str | None = None,
    source_sha256: str | None = None,
) -> Grain:
    cleaned = text.strip()
    trusted_flag = bool(trusted)
    return Grain(
        id=grain_id,
        text=cleaned,
        quadrant=quadrant,
        tags=[str(tag).lower() for tag in (tags or [])],
        embedding=hashed_bow_embedding(cleaned),
        symbol_ids=symbols_for(tokenize(cleaned)),
        tier=tier if tier in TIERS else "warm",
        protected=bool(protected) or trusted_flag,
        trusted=trusted_flag,
        source_name=source_name,
        source_sha256=source_sha256,
    )


@dataclass
class SandHit:
    grain: Grain
    score: float
    quadrant: str


@dataclass
class SandResult:
    hits: list[SandHit]
    quadrant_order: list[str]
    accessed_ids: list[str]
    accessed_quadrants: list[str]


class SandPit:
    """In-memory vector store. Optional JSON state file survives restarts."""

    def __init__(
        self,
        delay: float = 0.0,
        state_path: str | Path | None = None,
    ):
        self._grains: dict[str, Grain] = {}
        self.delay = delay
        self.state_path = Path(state_path) if state_path is not None else None
        self.rebuild_count = 0

    def rebuild_index(self) -> None:
        """Explicit rebuild hook. Not used when retagging."""
        self.rebuild_count += 1

    def load_seed(self, path: str | Path) -> int:
        with Path(path).open(encoding="utf-8") as handle:
            payload = json.load(handle)
        count = 0
        for entry in payload:
            source_name, source_sha = _source_fields(entry.get("source"))
            grain = make_grain(
                text=entry["text"],
                quadrant=entry["quadrant"],
                grain_id=entry["id"],
                tags=entry.get("tags") or [],
                tier=entry.get("tier", "warm"),
                protected=entry.get("protected", False),
                trusted=entry.get("trusted", False),
                source_name=source_name,
                source_sha256=source_sha,
            )
            self._grains[grain.id] = grain
            count += 1
        self._overlay_state()
        return count

    def _overlay_state(self) -> None:
        if self.state_path is None or not self.state_path.is_file():
            return
        with self.state_path.open(encoding="utf-8") as handle:
            state = json.load(handle)
        for entry in state.get("grains", []):
            existing = self._grains.get(entry["id"])
            if existing is None:
                source_name, source_sha = _source_fields(entry.get("source"))
                grain = make_grain(
                    text=entry["text"],
                    quadrant=entry["quadrant"],
                    grain_id=entry["id"],
                    tags=entry.get("tags") or [],
                    tier=entry.get("tier", "warm"),
                    protected=entry.get("protected", False),
                    trusted=entry.get("trusted", False),
                    source_name=source_name,
                    source_sha256=source_sha,
                )
                grain.hits = int(entry.get("hits", 0))
                grain.last_access = entry.get("last_access")
                grain.helped_count = int(entry.get("helped_count", 0))
                self._grains[grain.id] = grain
            else:
                existing.tier = entry.get("tier", existing.tier)
                existing.protected = bool(entry.get("protected", existing.protected))
                existing.hits = int(entry.get("hits", existing.hits))
                existing.last_access = entry.get("last_access", existing.last_access)
                existing.helped_count = int(entry.get("helped_count", existing.helped_count))
                if entry.get("quadrant"):
                    existing.quadrant = entry["quadrant"]
                if "trusted" in entry:
                    existing.trusted = bool(entry["trusted"])
                    if existing.trusted:
                        existing.protected = True
                source_name, source_sha = _source_fields(entry.get("source"))
                if source_name:
                    existing.source_name = source_name
                if source_sha:
                    existing.source_sha256 = source_sha

    def save_state(self) -> None:
        if self.state_path is None:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "grains": [
                {
                    "id": grain.id,
                    "text": grain.text,
                    "quadrant": grain.quadrant,
                    "tags": list(grain.tags),
                    "tier": grain.tier,
                    "protected": grain.protected,
                    "hits": grain.hits,
                    "last_access": grain.last_access,
                    "helped_count": grain.helped_count,
                    "trusted": grain.trusted,
                    "source": grain.source(),
                }
                for grain in self._grains.values()
            ]
        }
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self.state_path)

    def add(self, grain: Grain) -> Grain:
        self._grains[grain.id] = grain
        return grain

    def promote(self, grain: Grain) -> None:
        self._grains[grain.id] = grain
        self.save_state()

    def get(self, grain_id: str) -> Grain | None:
        return self._grains.get(grain_id)

    def all_grains(self) -> list[Grain]:
        return list(self._grains.values())

    def retag(self, grain_id: str, quadrant: str) -> None:
        grain = self._grains.get(grain_id)
        if grain is None:
            raise KeyError(f"unknown grain: {grain_id}")
        grain.quadrant = quadrant
        # Do not rebuild the index. Changing boundaries is a tag change only.

    def mark_helped(self, grain_id: str) -> None:
        grain = self._grains.get(grain_id)
        if grain is not None:
            grain.helped_count += 1

    def set_tier(self, grain_id: str, tier: str) -> None:
        if tier not in TIERS:
            raise ValueError(f"unknown tier: {tier}")
        grain = self._grains.get(grain_id)
        if grain is None:
            raise KeyError(f"unknown grain: {grain_id}")
        grain.tier = tier

    def search(
        self,
        query_vec: tuple[float, ...],
        quadrant_order: Iterable[str],
        k: int = 5,
    ) -> SandResult:
        if self.delay:
            time.sleep(self.delay)
        order = list(quadrant_order)
        rank = {qid: index for index, qid in enumerate(order)}
        scored: list[tuple[int, float, Grain]] = []
        for grain in self._grains.values():
            if grain.quadrant not in rank:
                continue
            score = cosine(query_vec, grain.embedding)
            scored.append((rank[grain.quadrant], -score, grain))
        scored.sort(key=lambda item: (item[0], item[1]))
        picked = scored[:k]
        now = time.time()
        hits: list[SandHit] = []
        accessed_quadrants: list[str] = []
        for _rank, neg_score, grain in picked:
            grain.hits += 1
            grain.last_access = now
            hits.append(SandHit(grain=grain, score=-neg_score, quadrant=grain.quadrant))
            if grain.quadrant not in accessed_quadrants:
                accessed_quadrants.append(grain.quadrant)
        return SandResult(
            hits=hits,
            quadrant_order=order,
            accessed_ids=[hit.grain.id for hit in hits],
            accessed_quadrants=accessed_quadrants,
        )

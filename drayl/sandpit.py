"""Sand pit: one vector store of grains, partitioned by a static quadrant map."""

from __future__ import annotations

import hashlib
import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


from drayl.embeddings import cosine, hashed_bow_embedding


@dataclass
class Grain:
    """One piece of stored content (a 'sand grain')."""

    id: str
    text: str
    embedding: tuple[float, ...]
    quadrant: str
    hits: int = 0
    last_access: float | None = None
    helped_count: int = 0
    trusted: bool = False
    source_name: str | None = None
    source_sha256: str | None = None
    superseded_by: str | None = None
    status: str = "active"  # "active", "superseded", or "conflict"
    teacher_id: str | None = None
    concept_key: str | None = None
    trust_provenance: dict | None = None

    def source(self) -> dict | None:
        if self.source_name or self.source_sha256:
            return {"name": self.source_name, "sha256": self.source_sha256}
        return None

    def record(self) -> dict:
        """Full grain record, including trusted/source metadata and heat."""
        return {
            "id": self.id,
            "text": self.text,
            "embedding": list(self.embedding),
            "quadrant": self.quadrant,
            "hits": self.hits,
            "last_access": self.last_access,
            "helped_count": self.helped_count,
            "trusted": self.trusted,
            "source": self.source(),
            "superseded_by": self.superseded_by,
            "status": self.status,
            "teacher_id": self.teacher_id,
            "concept_key": self.concept_key,
            "trust_provenance": dict(self.trust_provenance) if self.trust_provenance else None,
        }

    def heat(self) -> dict:
        return {
            "id": self.id,
            "quadrant": self.quadrant,
            "hits": self.hits,
            "last_access": self.last_access,
            "helped_count": self.helped_count,
            "trusted": self.trusted,
            "source": self.source(),
            "status": self.status,
        }

    @classmethod
    def from_record(cls, data: dict) -> "Grain":
        source = data.get("source") or {}
        if isinstance(source, str):
            source = {"name": source, "sha256": None}
        return cls(
            id=data["id"],
            text=data["text"],
            embedding=tuple(float(x) for x in data["embedding"]),
            quadrant=data["quadrant"],
            hits=int(data.get("hits", 0)),
            last_access=data.get("last_access"),
            helped_count=int(data.get("helped_count", 0)),
            trusted=bool(data.get("trusted", False)),
            source_name=source.get("name"),
            source_sha256=source.get("sha256"),
            superseded_by=data.get("superseded_by"),
            status=data.get("status", "active"),
            teacher_id=data.get("teacher_id"),
            concept_key=data.get("concept_key"),
            trust_provenance=data.get("trust_provenance"),
        )


def make_grain(
    text: str,
    quadrant: str,
    grain_id: str | None = None,
    trusted: bool = False,
    source_name: str | None = None,
    source_sha256: str | None = None,
    teacher_id: str | None = None,
    concept_key: str | None = None,
    trust_provenance: dict | None = None,
) -> Grain:
    embedding = hashed_bow_embedding(text)
    if grain_id is None:
        # Stable id from the text. When a source document is attached,
        # include its checksum so a trusted school-mode grain can sit
        # beside an untrusted grain of the same words until sleep.
        # Teacher injections also include teacher_id so two teachers can
        # hold different grains about the same concept until a conflict
        # is resolved.
        if source_sha256 or teacher_id:
            material = (
                f"{text}\n{source_sha256 or ''}\n"
                f"{'trusted' if trusted else 'untrusted'}\n"
                f"{teacher_id or ''}"
            )
        else:
            material = text
        grain_id = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
    return Grain(
        id=grain_id,
        text=text,
        embedding=embedding,
        quadrant=quadrant,
        trusted=trusted,
        source_name=source_name,
        source_sha256=source_sha256,
        teacher_id=teacher_id,
        concept_key=concept_key,
        trust_provenance=dict(trust_provenance) if trust_provenance else None,
    )


class SandPit(ABC):
    """Abstract sand pit interface. Implementations must not rebuild on retag."""

    @abstractmethod
    def search(
        self,
        query_vec: tuple[float, ...],
        quadrant_order: Iterable[str],
        k: int,
    ) -> list[Grain]:
        """Return up to k grains, preferring earlier quadrants in quadrant_order."""

    @abstractmethod
    def retag(self, grain_id: str, quadrant: str) -> None:
        """Move a grain to a new quadrant without rebuilding the index."""

    @abstractmethod
    def promote(self, grain: Grain) -> None:
        """Accept a grain that passed the compliance gate."""

    @abstractmethod
    def get(self, grain_id: str) -> Grain | None:
        """Look up one grain by id, including superseded grains."""

    @abstractmethod
    def all_grains(self, include_superseded: bool = False) -> list[Grain]:
        """Grains currently in the pit. Active only, unless asked."""

    @abstractmethod
    def heat_map(self) -> list[dict]:
        """Lived access stats: frequency, recency, and whether a grain helped."""

    @abstractmethod
    def query(
        self,
        trusted: bool | None = None,
        quadrant: str | None = None,
        source: str | None = None,
        include_superseded: bool = False,
    ) -> list[Grain]:
        """Filter grains by trusted flag, quadrant, and source name or sha256."""


class InMemorySandPit(SandPit):
    """Simple in-memory stub. The 'index' is just a dict of grains by id.

    Retag only changes the quadrant field. rebuild_index() exists so tests
    can prove it is not called on a boundary change. Superseded grains
    leave the active index but are kept, never silently deleted.
    """

    def __init__(self, delay: float = 0.0):
        self._grains: dict[str, Grain] = {}
        self._superseded: dict[str, Grain] = {}
        self.rebuild_count = 0
        self.delay = delay

    def rebuild_index(self) -> None:
        """Explicit rebuild hook. Not used when retagging."""
        self.rebuild_count += 1

    def promote(self, grain: Grain) -> None:
        grain.status = "active"
        grain.superseded_by = None
        self._superseded.pop(grain.id, None)
        self._grains[grain.id] = grain

    def add(
        self,
        text: str,
        quadrant: str,
        grain_id: str | None = None,
        trusted: bool = False,
        source_name: str | None = None,
        source_sha256: str | None = None,
        teacher_id: str | None = None,
        concept_key: str | None = None,
        trust_provenance: dict | None = None,
    ) -> Grain:
        grain = make_grain(
            text,
            quadrant,
            grain_id=grain_id,
            trusted=trusted,
            source_name=source_name,
            source_sha256=source_sha256,
            teacher_id=teacher_id,
            concept_key=concept_key,
            trust_provenance=trust_provenance,
        )
        self.promote(grain)
        return grain

    def get(self, grain_id: str) -> Grain | None:
        return self._grains.get(grain_id) or self._superseded.get(grain_id)

    def all_grains(self, include_superseded: bool = False) -> list[Grain]:
        grains = list(self._grains.values())
        if include_superseded:
            grains.extend(self._superseded.values())
        return grains

    def heat_map(self) -> list[dict]:
        return [grain.heat() for grain in self._grains.values()]

    def query(
        self,
        trusted: bool | None = None,
        quadrant: str | None = None,
        source: str | None = None,
        include_superseded: bool = False,
    ) -> list[Grain]:
        found: list[Grain] = []
        for grain in self.all_grains(include_superseded=include_superseded):
            if trusted is not None and grain.trusted is not trusted:
                continue
            if quadrant is not None and grain.quadrant != quadrant:
                continue
            if source is not None and not _source_matches(grain, source):
                continue
            found.append(grain)
        return found

    def retag(self, grain_id: str, quadrant: str) -> None:
        grain = self._grains.get(grain_id)
        if grain is None:
            raise KeyError(f"unknown grain: {grain_id}")
        grain.quadrant = quadrant
        # Do not call rebuild_index(). Changing boundaries is a tag change only.

    def mark_helped(self, grain_id: str) -> None:
        grain = self.get(grain_id)
        if grain is not None:
            grain.helped_count += 1

    def supersede(self, grain_id: str, trusted_id: str) -> Grain:
        """Demote an active grain. It is marked and kept, not deleted."""
        grain = self._grains.pop(grain_id, None)
        if grain is None:
            raise KeyError(f"unknown active grain: {grain_id}")
        grain.status = "superseded"
        grain.superseded_by = trusted_id
        self._superseded[grain.id] = grain
        return grain

    def park(self, grain_id: str, reason: str = "conflict") -> Grain:
        """Take an active grain out of the pit and keep it, never delete it.

        Used when two teachers disagree: both grains leave the active
        index until a human or a trusted source breaks the tie.
        """
        grain = self._grains.pop(grain_id, None)
        if grain is None:
            raise KeyError(f"unknown active grain: {grain_id}")
        grain.status = reason
        self._superseded[grain.id] = grain
        return grain

    def search(
        self,
        query_vec: tuple[float, ...],
        quadrant_order: Iterable[str],
        k: int,
    ) -> list[Grain]:
        if self.delay:
            time.sleep(self.delay)
        order = list(quadrant_order)
        rank = {qid: index for index, qid in enumerate(order)}
        # Only active grains in the requested quadrants are searched.
        scored: list[tuple[int, float, Grain]] = []
        for grain in self._grains.values():
            if grain.quadrant not in rank:
                continue
            score = cosine(query_vec, grain.embedding)
            scored.append((rank[grain.quadrant], -score, grain))
        scored.sort(key=lambda item: (item[0], item[1]))
        picked = [item[2] for item in scored[:k]]
        now = time.time()
        for grain in picked:
            grain.hits += 1
            grain.last_access = now
        return picked

    def replace_all(self, records: Iterable[dict]) -> None:
        """Reload every grain in place from stored records.

        Keeps each record's own status ("active", "superseded" or
        "conflict"). Used by the durable store after a failed save or when
        another process committed first. Callers holding this pit object
        (router, gate) see the reloaded grains.
        """
        grains: dict[str, Grain] = {}
        parked: dict[str, Grain] = {}
        for record in records:
            grain = Grain.from_record(record)
            if grain.status == "active":
                grains[grain.id] = grain
            else:
                parked[grain.id] = grain
        self._grains.clear()
        self._grains.update(grains)
        self._superseded.clear()
        self._superseded.update(parked)

    def save(self, path: str | Path) -> Path:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 38,
            "grains": [grain.record() for grain in self._grains.values()],
            "superseded": [grain.record() for grain in self._superseded.values()],
        }
        destination.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return destination

    @classmethod
    def load(cls, path: str | Path, delay: float = 0.0) -> "InMemorySandPit":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        pit = cls(delay=delay)
        for record in payload.get("grains", []):
            grain = Grain.from_record(record)
            pit._grains[grain.id] = grain
        for record in payload.get("superseded", []):
            grain = Grain.from_record(record)
            grain.status = "superseded"
            pit._superseded[grain.id] = grain
        return pit


def _source_matches(grain: Grain, source: str) -> bool:
    wanted = source.strip()
    if not wanted:
        return False
    names = [grain.source_name or "", grain.source_sha256 or ""]
    if wanted in names:
        return True
    # Allow matching a path by its file name.
    for name in names:
        if name and (name.endswith(wanted) or Path(name).name == Path(wanted).name):
            return True
    return False

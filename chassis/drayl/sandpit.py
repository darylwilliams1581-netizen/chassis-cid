"""Sand pit: one vector store of grains, partitioned by a static quadrant map."""

from __future__ import annotations

import hashlib
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
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

    def heat(self) -> dict:
        return {
            "id": self.id,
            "quadrant": self.quadrant,
            "hits": self.hits,
            "last_access": self.last_access,
            "helped_count": self.helped_count,
        }


def make_grain(text: str, quadrant: str, grain_id: str | None = None) -> Grain:
    embedding = hashed_bow_embedding(text)
    if grain_id is None:
        # Stable id from the text itself so the same words get the same id.
        grain_id = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return Grain(id=grain_id, text=text, embedding=embedding, quadrant=quadrant)


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
        """Look up one grain by id."""

    @abstractmethod
    def all_grains(self) -> list[Grain]:
        """Every grain currently in the pit."""

    @abstractmethod
    def heat_map(self) -> list[dict]:
        """Lived access stats: frequency, recency, and whether a grain helped."""


class InMemorySandPit(SandPit):
    """Simple in-memory stub. The 'index' is just a dict of grains by id.

    Retag only changes the quadrant field. rebuild_index() exists so tests
    can prove it is not called on a boundary change.
    """

    def __init__(self, delay: float = 0.0):
        self._grains: dict[str, Grain] = {}
        self.rebuild_count = 0
        self.delay = delay

    def rebuild_index(self) -> None:
        """Explicit rebuild hook. Not used when retagging."""
        self.rebuild_count += 1

    def promote(self, grain: Grain) -> None:
        self._grains[grain.id] = grain

    def add(self, text: str, quadrant: str, grain_id: str | None = None) -> Grain:
        grain = make_grain(text, quadrant, grain_id=grain_id)
        self.promote(grain)
        return grain

    def get(self, grain_id: str) -> Grain | None:
        return self._grains.get(grain_id)

    def all_grains(self) -> list[Grain]:
        return list(self._grains.values())

    def heat_map(self) -> list[dict]:
        return [grain.heat() for grain in self._grains.values()]

    def retag(self, grain_id: str, quadrant: str) -> None:
        grain = self._grains.get(grain_id)
        if grain is None:
            raise KeyError(f"unknown grain: {grain_id}")
        grain.quadrant = quadrant
        # Do not call rebuild_index(). Changing boundaries is a tag change only.

    def mark_helped(self, grain_id: str) -> None:
        grain = self._grains.get(grain_id)
        if grain is not None:
            grain.helped_count += 1

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
        # Only grains in the requested quadrants are searched. The router
        # always passes the full map (biased first, the rest after), so
        # every grain stays reachable without rebuilding.
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

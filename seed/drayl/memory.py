"""Memory arm of the base layer.

One program, two stores: a fast context store and a slower retrieval store.
Recall searches both stores at the same time. Memory also keeps
cross-quadrant grain associations and a usage heat map for them.

Memory sits *inside* the base layer (one of the two parallel arms).
It is not a program above the base layer, even though the P33 list
numbered it with those programs. See the README for that design note.
"""

from __future__ import annotations

import hashlib
import time
from abc import ABC, abstractmethod
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from drayl.embeddings import cosine, hashed_bow_embedding


@dataclass
class MemoryTrace:
    """What mattered the last time a similar question was asked."""

    id: str
    question: str
    embedding: tuple[float, ...]
    what_mattered: str
    helped_quadrants: list[str]
    timestamp: float


@dataclass
class AssociationHeat:
    """Usage heat for a semantic link between two grains, possibly across quadrants."""

    grain_a: str
    grain_b: str
    consulted: int = 0
    helped: int = 0


@dataclass
class MemoryResult:
    """Context returned to the router to merge with sand grains."""

    context: str
    traces: list[MemoryTrace] = field(default_factory=list)
    source: str = ""  # "fast", "slow", "both", or "none"
    helped_quadrants: list[str] = field(default_factory=list)


class Memory(ABC):
    """Abstract memory interface."""

    @abstractmethod
    def remember(
        self,
        question: str,
        what_mattered: str,
        helped_quadrants: list[str] | None = None,
    ) -> MemoryTrace:
        """Record what mattered for a question."""

    @abstractmethod
    def recall(self, question: str) -> MemoryResult:
        """Look up context for a similar past question."""

    @abstractmethod
    def bias_order(self, question: str, default_order: list[str]) -> list[str]:
        """Cheap peek at the fast store: which quadrants to search first."""

    @abstractmethod
    def associate(self, grain_a: str, grain_b: str, helped: bool = False) -> AssociationHeat:
        """Record (or bump the heat of) a link between two grains."""


class InMemoryMemory(Memory):
    """In-memory stub with a fast store and a slower archive store."""

    def __init__(
        self,
        delay: float = 0.0,
        default_quadrants: list[str] | None = None,
        fast_limit: int = 32,
        similarity_floor: float = 0.25,
    ):
        self.fast_context: OrderedDict[str, MemoryTrace] = OrderedDict()
        self.slow_retrieval: OrderedDict[str, MemoryTrace] = OrderedDict()
        self.associations: dict[frozenset[str], AssociationHeat] = {}
        self.delay = delay
        self.default_quadrants = list(default_quadrants or [])
        self.fast_limit = fast_limit
        self.similarity_floor = similarity_floor

    def remember(
        self,
        question: str,
        what_mattered: str,
        helped_quadrants: list[str] | None = None,
    ) -> MemoryTrace:
        trace_id = hashlib.sha256(
            f"{question}\n{what_mattered}".encode("utf-8")
        ).hexdigest()[:16]
        trace = MemoryTrace(
            id=trace_id,
            question=question,
            embedding=hashed_bow_embedding(question),
            what_mattered=what_mattered,
            helped_quadrants=list(helped_quadrants or []),
            timestamp=time.time(),
        )
        self.fast_context[trace.id] = trace
        self.fast_context.move_to_end(trace.id)
        while len(self.fast_context) > self.fast_limit:
            self.fast_context.popitem(last=False)
        self.slow_retrieval[trace.id] = trace
        return trace

    def bias_order(self, question: str, default_order: list[str]) -> list[str]:
        """Fast-store only. No artificial delay, so the router can peek before dispatch."""
        match = self._best_match(list(self.fast_context.values()), hashed_bow_embedding(question))
        if match is None or not match.helped_quadrants:
            return list(default_order)
        order: list[str] = []
        seen: set[str] = set()
        for qid in list(match.helped_quadrants) + list(default_order):
            if qid not in seen:
                order.append(qid)
                seen.add(qid)
        return order

    def recall(self, question: str) -> MemoryResult:
        vec = hashed_bow_embedding(question)
        # Two threads, one program: search the fast store and the slow store together.
        with ThreadPoolExecutor(max_workers=2) as pool:
            fast_future = pool.submit(
                self._search_store, list(self.fast_context.values()), vec, "fast"
            )
            slow_future = pool.submit(
                self._search_store, list(self.slow_retrieval.values()), vec, "slow"
            )
            fast_hits = fast_future.result()
            slow_hits = slow_future.result()
        # Merge by similarity, not by which store answered first. A perfect
        # match sitting only in the slower store must still win.
        fast_ids = {trace.id for trace in fast_hits}
        slow_ids = {trace.id for trace in slow_hits}
        scored: dict[str, tuple[float, MemoryTrace, set[str]]] = {}
        for trace in fast_hits + slow_hits:
            sources = scored[trace.id][2] if trace.id in scored else set()
            if trace.id in fast_ids:
                sources.add("fast")
            if trace.id in slow_ids:
                sources.add("slow")
            score = cosine(vec, trace.embedding)
            scored[trace.id] = (score, trace, sources)
        ranked = sorted(scored.values(), key=lambda item: item[0], reverse=True)
        traces = [trace for _score, trace, _sources in ranked]
        if not traces:
            return MemoryResult(context="", traces=[], source="none")
        top = traces[0]
        top_sources = scored[top.id][2]
        if top_sources == {"fast", "slow"}:
            source_label = "both"
        else:
            source_label = next(iter(top_sources))
        return MemoryResult(
            context=top.what_mattered,
            traces=traces,
            source=source_label,
            helped_quadrants=list(top.helped_quadrants),
        )

    def associate(self, grain_a: str, grain_b: str, helped: bool = False) -> AssociationHeat:
        if grain_a == grain_b:
            raise ValueError("an association needs two different grain ids")
        key = frozenset({grain_a, grain_b})
        heat = self.associations.get(key)
        if heat is None:
            heat = AssociationHeat(grain_a=grain_a, grain_b=grain_b)
            self.associations[key] = heat
        heat.consulted += 1
        if helped:
            heat.helped += 1
        return heat

    def _search_store(
        self, traces: list[MemoryTrace], query_vec: tuple[float, ...], _label: str
    ) -> list[MemoryTrace]:
        if self.delay:
            time.sleep(self.delay)
        ranked: list[tuple[float, MemoryTrace]] = []
        for trace in traces:
            score = cosine(query_vec, trace.embedding)
            if score >= self.similarity_floor:
                ranked.append((score, trace))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [trace for _score, trace in ranked]

    def _best_match(
        self, traces: list[MemoryTrace], query_vec: tuple[float, ...]
    ) -> MemoryTrace | None:
        best: MemoryTrace | None = None
        best_score = self.similarity_floor
        for trace in traces:
            score = cosine(query_vec, trace.embedding)
            if score >= best_score:
                best = trace
                best_score = score
        return best


    def save(self, path: str | Path) -> Path:
        """Write traces and associations to a JSON file so runs can accumulate."""
        from pathlib import Path as _Path
        import json as _json

        destination = _Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 38,
            "traces": [
                {
                    "id": trace.id,
                    "question": trace.question,
                    "what_mattered": trace.what_mattered,
                    "helped_quadrants": list(trace.helped_quadrants),
                    "embedding": list(trace.embedding),
                    "timestamp": trace.timestamp,
                }
                for trace in self.slow_retrieval.values()
            ],
            "fast_ids": list(self.fast_context.keys()),
            "associations": [
                {
                    "grain_a": heat.grain_a,
                    "grain_b": heat.grain_b,
                    "consulted": heat.consulted,
                    "helped": heat.helped,
                }
                for heat in self.associations.values()
            ],
        }
        destination.write_text(_json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return destination

    @classmethod
    def load(cls, path: str | Path, **kwargs) -> "InMemoryMemory":
        from pathlib import Path as _Path
        import json as _json

        payload = _json.loads(_Path(path).read_text(encoding="utf-8"))
        memory = cls(**kwargs)
        for record in payload.get("traces", []):
            trace = MemoryTrace(
                id=record["id"],
                question=record["question"],
                embedding=tuple(float(x) for x in record.get("embedding", ())),
                what_mattered=record.get("what_mattered", ""),
                helped_quadrants=list(record.get("helped_quadrants") or []),
                timestamp=float(record.get("timestamp") or 0.0),
            )
            if not trace.embedding:
                trace.embedding = hashed_bow_embedding(trace.question)
            memory.slow_retrieval[trace.id] = trace
        for trace_id in payload.get("fast_ids", []):
            trace = memory.slow_retrieval.get(trace_id)
            if trace is not None:
                memory.fast_context[trace.id] = trace
        for record in payload.get("associations", []):
            memory.associate(record["grain_a"], record["grain_b"], helped=False)
            key = frozenset({record["grain_a"], record["grain_b"]})
            heat = memory.associations[key]
            # associate() already bumped consulted by 1; restore stored counts.
            heat.consulted = int(record.get("consulted", heat.consulted))
            heat.helped = int(record.get("helped", 0))
        return memory

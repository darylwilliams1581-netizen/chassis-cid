"""Memory arm: SQLite store of past queries and what helped.

One program, two stores: a fast context table (recent traces) and a
slower retrieval table (the full archive). Recall can search both.
A cheap peek at the fast store can bias which quadrant the sand pit
searches first. That peek is not delayed, so it can run before the
parallel dispatch without spoiling the timing.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from drayl_chat.embeddings import cosine, hashed_bow_embedding


def _pack_vec(vec: tuple[float, ...]) -> str:
    return json.dumps(list(vec))


def _unpack_vec(payload: str) -> tuple[float, ...]:
    return tuple(json.loads(payload))


@dataclass
class MemoryTrace:
    """What mattered the last time a similar question was asked."""

    id: str
    question: str
    what_mattered: str
    helped_grain_ids: list[str]
    helped_quadrants: list[str]
    timestamp: float
    score: float = 0.0
    embedding: tuple[float, ...] = field(default_factory=tuple, repr=False)


@dataclass
class MemoryResult:
    context: str
    traces: list[MemoryTrace] = field(default_factory=list)
    source: str = "none"  # "fast", "slow", "both", or "none"
    helped_grain_ids: list[str] = field(default_factory=list)
    helped_quadrants: list[str] = field(default_factory=list)


class SqliteMemory:
    """SQLite-backed memory. Safe for the local web server via a lock."""

    def __init__(
        self,
        path: str | Path,
        delay: float = 0.0,
        fast_limit: int = 32,
        similarity_floor: float = 0.25,
    ):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.delay = delay
        self.fast_limit = fast_limit
        self.similarity_floor = similarity_floor
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._setup()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _setup(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS traces (
                    id TEXT PRIMARY KEY,
                    question TEXT NOT NULL,
                    what_mattered TEXT NOT NULL,
                    helped_grain_ids TEXT NOT NULL,
                    helped_quadrants TEXT NOT NULL,
                    embedding TEXT NOT NULL,
                    timestamp REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS fast_context (
                    id TEXT PRIMARY KEY,
                    timestamp REAL NOT NULL,
                    FOREIGN KEY (id) REFERENCES traces(id)
                );
                CREATE TABLE IF NOT EXISTS associations (
                    grain_a TEXT NOT NULL,
                    grain_b TEXT NOT NULL,
                    consulted INTEGER NOT NULL DEFAULT 0,
                    helped INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (grain_a, grain_b)
                );
                """
            )
            self._conn.commit()

    def remember(
        self,
        question: str,
        what_mattered: str,
        helped_grain_ids: list[str] | None = None,
        helped_quadrants: list[str] | None = None,
    ) -> MemoryTrace:
        trace = MemoryTrace(
            id=str(uuid.uuid4()),
            question=question,
            what_mattered=what_mattered,
            helped_grain_ids=list(helped_grain_ids or []),
            helped_quadrants=list(helped_quadrants or []),
            timestamp=time.time(),
            embedding=hashed_bow_embedding(question),
        )
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO traces (
                    id, question, what_mattered, helped_grain_ids,
                    helped_quadrants, embedding, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trace.id,
                    trace.question,
                    trace.what_mattered,
                    json.dumps(trace.helped_grain_ids),
                    json.dumps(trace.helped_quadrants),
                    _pack_vec(trace.embedding),
                    trace.timestamp,
                ),
            )
            self._conn.execute(
                "INSERT INTO fast_context (id, timestamp) VALUES (?, ?)",
                (trace.id, trace.timestamp),
            )
            # Keep only the newest fast_limit rows in the fast store.
            self._conn.execute(
                """
                DELETE FROM fast_context
                WHERE id NOT IN (
                    SELECT id FROM fast_context ORDER BY timestamp DESC LIMIT ?
                )
                """,
                (self.fast_limit,),
            )
            self._conn.commit()
        return trace

    def count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM traces").fetchone()
        return int(row["n"]) if row else 0

    def bias_order(self, question: str, default_order: list[str]) -> list[str]:
        """Cheap peek at the fast store only. No artificial delay."""
        vec = hashed_bow_embedding(question)
        match = self._best_match(self._load_fast(), vec)
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
        # Artificial delay (tests only) sits on the full recall path, not
        # on bias_order, so the router can peek and then dispatch in parallel.
        if self.delay:
            time.sleep(self.delay)
        fast_hits = self._search_store(self._load_fast(), vec)
        slow_hits = self._search_store(self._load_slow(), vec)
        traces_by_id: dict[str, MemoryTrace] = {}
        sources: set[str] = set()
        for trace in fast_hits:
            if trace.id not in traces_by_id:
                traces_by_id[trace.id] = trace
                sources.add("fast")
        for trace in slow_hits:
            if trace.id not in traces_by_id:
                traces_by_id[trace.id] = trace
                sources.add("slow")
            elif "fast" in sources:
                sources.add("slow")
        traces = list(traces_by_id.values())
        if not traces:
            return MemoryResult(context="", traces=[], source="none")
        if sources >= {"fast", "slow"}:
            source_label = "both"
        else:
            source_label = next(iter(sources))
        top = traces[0]
        helped_ids: list[str] = []
        helped_quads: list[str] = []
        for trace in traces:
            for gid in trace.helped_grain_ids:
                if gid not in helped_ids:
                    helped_ids.append(gid)
            for qid in trace.helped_quadrants:
                if qid not in helped_quads:
                    helped_quads.append(qid)
        return MemoryResult(
            context=top.what_mattered,
            traces=traces,
            source=source_label,
            helped_grain_ids=helped_ids,
            helped_quadrants=helped_quads,
        )

    def associate(self, grain_a: str, grain_b: str, helped: bool = False) -> None:
        if grain_a == grain_b:
            raise ValueError("an association needs two different grain ids")
        a, b = sorted((grain_a, grain_b))
        with self._lock:
            row = self._conn.execute(
                "SELECT consulted, helped FROM associations WHERE grain_a=? AND grain_b=?",
                (a, b),
            ).fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO associations (grain_a, grain_b, consulted, helped) VALUES (?, ?, 1, ?)",
                    (a, b, 1 if helped else 0),
                )
            else:
                self._conn.execute(
                    """
                    UPDATE associations
                    SET consulted = consulted + 1, helped = helped + ?
                    WHERE grain_a=? AND grain_b=?
                    """,
                    (1 if helped else 0, a, b),
                )
            self._conn.commit()

    def _row_to_trace(self, row: sqlite3.Row, score: float = 0.0) -> MemoryTrace:
        return MemoryTrace(
            id=row["id"],
            question=row["question"],
            what_mattered=row["what_mattered"],
            helped_grain_ids=json.loads(row["helped_grain_ids"]),
            helped_quadrants=json.loads(row["helped_quadrants"]),
            timestamp=row["timestamp"],
            score=score,
            embedding=_unpack_vec(row["embedding"]),
        )

    def _load_fast(self) -> list[MemoryTrace]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT t.* FROM traces t
                INNER JOIN fast_context f ON f.id = t.id
                ORDER BY t.timestamp DESC
                """
            ).fetchall()
        return [self._row_to_trace(row) for row in rows]

    def _load_slow(self) -> list[MemoryTrace]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM traces ORDER BY timestamp DESC"
            ).fetchall()
        return [self._row_to_trace(row) for row in rows]

    def _search_store(
        self, traces: list[MemoryTrace], query_vec: tuple[float, ...]
    ) -> list[MemoryTrace]:
        ranked: list[MemoryTrace] = []
        for trace in traces:
            score = cosine(query_vec, trace.embedding)
            if score >= self.similarity_floor:
                ranked.append(
                    MemoryTrace(
                        id=trace.id,
                        question=trace.question,
                        what_mattered=trace.what_mattered,
                        helped_grain_ids=list(trace.helped_grain_ids),
                        helped_quadrants=list(trace.helped_quadrants),
                        timestamp=trace.timestamp,
                        score=score,
                        embedding=trace.embedding,
                    )
                )
        ranked.sort(key=lambda item: item.score, reverse=True)
        return ranked

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

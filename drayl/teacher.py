"""Teacher arm: inject grains from an external AI teacher (P39).

The service sits beside the router. Teachers call in; this code never
calls out to an AI. A teacher may *ask* for the trusted flag, but the
flag only sticks if you have put that teacher_id on the local allowlist.
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from drayl.embeddings import cosine, hashed_bow_embedding, tokenize
from drayl.gate import ComplianceGate, GateDecision
from drayl.pit_status import PitStatus
from drayl.quadrants import QuadrantMap, load_quadrant_map
from drayl.sandpit import Grain, InMemorySandPit, make_grain
from drayl.store import DurableStore, PersistenceError

# Same concept if two grains share a concept key, or their vectors are
# at least this similar.
DEFAULT_CONCEPT_COSINE = 0.82
# If they share a concept key but have little wording in common, treat
# the claims as disagreeing (paraphrases of the *same* fact may also
# land here, which is why a human or a trusted source must break the tie).
DEFAULT_LOW_AGREEMENT = 0.4

STOP = frozenset(
    {
        "a",
        "an",
        "the",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "of",
        "in",
        "on",
        "at",
        "to",
        "for",
        "and",
        "or",
        "that",
        "this",
        "it",
        "as",
        "with",
        "by",
        "from",
        "its",
    }
)
NEGATION = frozenset(
    {
        "not",
        "no",
        "never",
        "none",
        "neither",
        "nor",
        "cannot",
        "cant",
        "dont",
        "doesnt",
        "isnt",
        "wasnt",
        "wont",
        "without",
        "false",
        "incorrect",
    }
)
NUMBER_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")
NEGATION_CONTRACTED = re.compile(r"n['’]t\b")

DEFAULT_LIMITS = {
    "max_batch_size": 8,
    "rate_per_second": 4.0,
    "burst": 16,
    "max_queue": 32,
}

REASON_ALLOWLISTED = "allowlisted teacher"
REASON_NOT_GRANTED = "trust requested, not granted"
REASON_UNTRUSTED = "untrusted"
REASON_SCHOOL = "school-mode document"


def derive_concept_key(text: str) -> str:
    """A short topic key: content words, no numbers and no negation."""
    tokens: list[str] = []
    for token in tokenize(text):
        if NUMBER_RE.fullmatch(token):
            continue
        if token in NEGATION or token in STOP:
            continue
        tokens.append(token)
    return " ".join(tokens[:12])


def extract_numbers(text: str) -> tuple[str, ...]:
    return tuple(NUMBER_RE.findall(text))


def has_negation(text: str) -> bool:
    if NEGATION_CONTRACTED.search(text.lower()):
        return True
    return any(token in NEGATION for token in tokenize(text))


def _jaccard(left: Iterable[str], right: Iterable[str]) -> float:
    a, b = set(left), set(right)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _normalised(text: str) -> str:
    return " ".join(text.split()).casefold()


def _content_tokens(text: str) -> list[str]:
    return [t for t in tokenize(text) if t not in STOP]


_COPULA = re.compile(r"\b(?:is|are|equals?|means?)\b", re.IGNORECASE)


def _predicate(text: str) -> str | None:
    """The claim after a simple copula, e.g. 'is positive'."""
    parts = _COPULA.split(text, maxsplit=1)
    if len(parts) == 2 and parts[1].strip():
        return _normalised(parts[1])
    return None


def _is_submultiset(small: Counter, big: Counter) -> bool:
    return all(small[key] <= big[key] for key in small)


def contradiction_reason(
    text_a: str,
    text_b: str,
    key_a: str,
    key_b: str,
    cosine_score: float,
    concept_cosine: float = DEFAULT_CONCEPT_COSINE,
    low_agreement: float = DEFAULT_LOW_AGREEMENT,
) -> str | None:
    """Return a short reason if the two texts disagree on the same concept."""
    keys_match = bool(key_a and key_b and key_a == key_b)
    similar = cosine_score >= concept_cosine
    if not (keys_match or similar):
        return None

    nums_a, nums_b = extract_numbers(text_a), extract_numbers(text_b)
    if nums_a and nums_b and nums_a != nums_b:
        return "differing_numbers"

    if has_negation(text_a) != has_negation(text_b):
        return "negation"

    na, nb = _normalised(text_a), _normalised(text_b)
    if na == nb:
        return None
    if na in nb or nb in na:
        # One text is an elaboration of the other, not a disagreement.
        return None

    pred_a, pred_b = _predicate(text_a), _predicate(text_b)
    if pred_a and pred_b and pred_a != pred_b:
        # "is negative" vs "is positive" — same frame, different claim.
        # Ignore if one predicate simply extends the other with more detail.
        if pred_a not in pred_b and pred_b not in pred_a:
            return "conflicting_claim"

    counts_a = Counter(_content_tokens(text_a))
    counts_b = Counter(_content_tokens(text_b))
    if counts_a != counts_b and not (
        _is_submultiset(counts_a, counts_b) or _is_submultiset(counts_b, counts_a)
    ):
        # High overlap with a different residual is a conflicting claim.
        # Low overlap with the same concept key is the "low agreement" case.
        set_a, set_b = set(counts_a), set(counts_b)
        if _jaccard(set_a, set_b) >= 0.5 or similar:
            return "conflicting_claim"
        if keys_match and _jaccard(set_a, set_b) < low_agreement:
            return "low_agreement"
    return None


def same_concept(
    grain_a: Grain,
    grain_b: Grain,
    concept_cosine: float = DEFAULT_CONCEPT_COSINE,
) -> bool:
    key_a = grain_a.concept_key or derive_concept_key(grain_a.text)
    key_b = grain_b.concept_key or derive_concept_key(grain_b.text)
    if key_a and key_b and key_a == key_b:
        return True
    return cosine(grain_a.embedding, grain_b.embedding) >= concept_cosine


@dataclass
class TeacherLimits:
    max_batch_size: int = DEFAULT_LIMITS["max_batch_size"]
    rate_per_second: float = DEFAULT_LIMITS["rate_per_second"]
    burst: int = DEFAULT_LIMITS["burst"]
    max_queue: int = DEFAULT_LIMITS["max_queue"]

    @classmethod
    def from_file(cls, path: str | Path | None) -> "TeacherLimits":
        if path is None or not Path(path).is_file():
            return cls()
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            max_batch_size=int(data.get("max_batch_size", cls.max_batch_size)),
            rate_per_second=float(data.get("rate_per_second", cls.rate_per_second)),
            burst=int(data.get("burst", cls.burst)),
            max_queue=int(data.get("max_queue", cls.max_queue)),
        )


@dataclass
class Injection:
    """One grain a teacher wants to add."""

    text: str
    quadrant: str
    teacher_id: str
    trusted: bool = False
    concept_key: str | None = None
    tags: tuple[str, ...] = ()
    block_id: str = ""
    source_name: str | None = None


@dataclass
class InjectResult:
    status: str
    http_status: int
    message: str = ""
    grain: Grain | None = None
    decision: GateDecision | None = None
    conflict_id: str | None = None
    trust_provenance: dict | None = None
    queue_position: int | None = None
    queued_count: int | None = None

    def as_dict(self) -> dict:
        payload = {
            "status": self.status,
            "http_status": self.http_status,
            "message": self.message,
            "conflict_id": self.conflict_id,
            "trust_provenance": self.trust_provenance,
            "queue_position": self.queue_position,
            "queued_count": self.queued_count,
            "grain": self.grain.record() if self.grain is not None else None,
            "gate_reason": self.decision.reason if self.decision is not None else None,
            "accepted": bool(self.decision.accepted) if self.decision is not None else None,
        }
        return payload


@dataclass
class BatchResult:
    status: str
    http_status: int
    message: str = ""
    items: list[InjectResult] = field(default_factory=list)
    queued_count: int | None = None
    queue_position: int | None = None
    # Durable store only: True once the change is committed to disk,
    # False if it was not saved. None when the arm has no store.
    saved: bool | None = None
    store_generation: int | None = None

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "http_status": self.http_status,
            "message": self.message,
            "queued_count": self.queued_count,
            "queue_position": self.queue_position,
            "saved": self.saved,
            "store_generation": self.store_generation,
            "items": [item.as_dict() for item in self.items],
        }


@dataclass
class _Txn:
    """One outer store transaction, shared by any nested mutations."""

    op: str
    generation: int | None = None
    staged: list[dict] = field(default_factory=list)


def _grain_signature(record: dict) -> str:
    # The embedding is derived from the text and never changes, so leave
    # it out of the change check to keep the diff cheap.
    return json.dumps(
        {k: v for k, v in record.items() if k != "embedding"},
        sort_keys=True,
        separators=(",", ":"),
    )


@dataclass
class _QueuedBatch:
    injections: list[Injection]
    enqueued_at: float
    teacher_id: str
    size: int


class _TokenBucket:
    def __init__(self, rate: float, burst: int):
        self.rate = float(rate)
        self.burst = float(burst)
        self.tokens = float(burst)
        self.updated = time.monotonic()
        self._lock = threading.Lock()

    def allow(self, count: int = 1) -> bool:
        with self._lock:
            now = time.monotonic()
            elapsed = now - self.updated
            self.updated = now
            self.tokens = min(self.burst, self.tokens + elapsed * self.rate)
            if self.tokens >= count:
                self.tokens -= count
                return True
            return False


def load_allowlist(path: str | Path | None) -> set[str]:
    if path is None or not Path(path).is_file():
        return set()
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, list):
        return {str(item).strip() for item in data if str(item).strip()}
    teachers = data.get("teachers") or data.get("allowlist") or []
    return {str(item).strip() for item in teachers if str(item).strip()}


def apply_trust_policy(teacher_id: str, requested: bool, allowlist: set[str]) -> dict:
    """Decide whether a requested trusted flag is actually granted."""
    if requested and teacher_id in allowlist:
        return {
            "teacher_id": teacher_id,
            "trusted_requested": True,
            "trusted_granted": True,
            "reason": REASON_ALLOWLISTED,
        }
    if requested:
        return {
            "teacher_id": teacher_id,
            "trusted_requested": True,
            "trusted_granted": False,
            "reason": REASON_NOT_GRANTED,
        }
    return {
        "teacher_id": teacher_id,
        "trusted_requested": False,
        "trusted_granted": False,
        "reason": REASON_UNTRUSTED,
    }


class TeacherArm:
    """In-process teacher arm. The HTTP socket is a thin wrapper around this."""

    def __init__(
        self,
        sandpit: InMemorySandPit,
        gate: ComplianceGate,
        quadrant_map: QuadrantMap | None = None,
        allowlist_path: str | Path | None = None,
        allowlist: Iterable[str] | None = None,
        limits: TeacherLimits | None = None,
        pit_status: PitStatus | None = None,
        conflicts_path: str | Path | None = None,
        concept_cosine: float = DEFAULT_CONCEPT_COSINE,
        low_agreement: float = DEFAULT_LOW_AGREEMENT,
        store: DurableStore | None = None,
    ):
        self.sandpit = sandpit
        self.gate = gate
        self.quadrant_map = quadrant_map or load_quadrant_map()
        self.allowlist_path = Path(allowlist_path) if allowlist_path else None
        self._allowlist_override = (
            {str(item).strip() for item in allowlist if str(item).strip()}
            if allowlist is not None
            else None
        )
        self.limits = limits or TeacherLimits()
        self.pit_status = pit_status or PitStatus()
        self.conflicts_path = (
            Path(conflicts_path) if conflicts_path is not None else Path("data/conflicts.jsonl")
        )
        self.conflicts_path.parent.mkdir(parents=True, exist_ok=True)
        self.concept_cosine = concept_cosine
        self.low_agreement = low_agreement
        self._lock = threading.RLock()
        self._buckets: dict[str, _TokenBucket] = {}
        self._queue: list[_QueuedBatch] = []
        self._conflicts: list[dict] = []
        # Durable store (optional). Without it the arm behaves exactly as
        # in P39: state lives in memory and the CLI saves it on Ctrl+C.
        self.store = store
        self._txn: _Txn | None = None
        self._txn_depth = 0
        self._generation = -1
        self._committed_grains: dict[str, tuple[str, str, str | None]] = {}
        self._committed_conflicts: dict[str, tuple[str, str]] = {}
        if self.store is None:
            self._load_conflicts()
        else:
            self._reload_from_store()
            self.gate.stage_sink = self._buffer_staged
        # Drain queued work when sleep or a rebuild finishes.
        self.pit_status.on_idle = self.drain
        # School-mode (or any) trusted promotion can break a tie later.
        previous = self.gate.on_promoted

        def _hook(grain: Grain) -> None:
            if previous is not None:
                previous(grain)
            self.consider_trusted_grain(grain)

        self.gate.on_promoted = _hook

    def allowlist(self) -> set[str]:
        if self._allowlist_override is not None:
            return set(self._allowlist_override)
        return load_allowlist(self.allowlist_path)

    def _bucket(self, teacher_id: str) -> _TokenBucket:
        bucket = self._buckets.get(teacher_id)
        if bucket is None:
            bucket = _TokenBucket(self.limits.rate_per_second, self.limits.burst)
            self._buckets[teacher_id] = bucket
        return bucket

    def inject(
        self,
        text: str,
        quadrant: str,
        teacher_id: str,
        trusted: bool = False,
        concept_key: str | None = None,
        tags: tuple[str, ...] = (),
        block_id: str = "",
        source_name: str | None = None,
        skip_rate_limit: bool = False,
        skip_busy: bool = False,
    ) -> InjectResult:
        """Inject one grain. This is the Python API for in-process use."""
        result = self.inject_many(
            [
                Injection(
                    text=text,
                    quadrant=quadrant,
                    teacher_id=teacher_id,
                    trusted=trusted,
                    concept_key=concept_key,
                    tags=tags,
                    block_id=block_id,
                    source_name=source_name,
                )
            ],
            skip_rate_limit=skip_rate_limit,
            skip_busy=skip_busy,
        )
        if result.items:
            return result.items[0]
        return InjectResult(
            status=result.status,
            http_status=result.http_status,
            message=result.message,
            queue_position=result.queue_position,
            queued_count=result.queued_count,
        )

    def inject_many(
        self,
        injections: list[Injection],
        skip_rate_limit: bool = False,
        skip_busy: bool = False,
    ) -> BatchResult:
        if not injections:
            return BatchResult(
                status="empty_batch",
                http_status=400,
                message="no grains in the request",
            )

        # Every item in a request is attributed to a teacher. Mixed
        # teacher_ids in one batch are allowed, but rate limit uses the
        # first id plus the batch size (volume, not connection count).
        if len(injections) > self.limits.max_batch_size:
            return BatchResult(
                status="batch_too_large",
                http_status=400,
                message=(
                    f"batch of {len(injections)} exceeds max_batch_size "
                    f"{self.limits.max_batch_size}"
                ),
            )

        teacher_id = injections[0].teacher_id
        if not teacher_id or not str(teacher_id).strip():
            return BatchResult(
                status="missing_teacher_id",
                http_status=400,
                message="each injection needs a teacher_id",
            )
        if any(not item.teacher_id or not item.teacher_id.strip() for item in injections):
            return BatchResult(
                status="missing_teacher_id",
                http_status=400,
                message="each injection needs a teacher_id",
            )

        with self._lock:
            if not skip_rate_limit and not self._bucket(teacher_id).allow(len(injections)):
                return BatchResult(
                    status="rate_limited",
                    http_status=429,
                    message=f"rate limit exceeded for teacher {teacher_id}",
                )

            if not skip_busy and self.pit_status.busy:
                result = self._enqueue(injections, teacher_id)
                if self.store is not None:
                    # Queued work is held in memory only and is not saved.
                    result.saved = False
                return result

            outer = self._txn_depth == 0
            try:
                with self._mutation(f"inject teacher={teacher_id} n={len(injections)}") as txn:
                    items = [self._accept_one(item) for item in injections]
            except PersistenceError as exc:
                return BatchResult(
                    status="not_saved",
                    http_status=503,
                    message=f"NOT saved; nothing was promoted or staged: {exc}",
                    saved=False,
                )
        # Pick a batch-level status: conflict wins if any, else first item.
        if any(item.status == "conflict" for item in items):
            status = "conflict"
            http_status = 409
        else:
            status = items[0].status if len(items) == 1 else "ok"
            http_status = items[0].http_status if len(items) == 1 else 200
        result = BatchResult(status=status, http_status=http_status, items=items)
        if self.store is not None and outer:
            result.saved = True
            result.store_generation = txn.generation
        return result

    def _enqueue(self, injections: list[Injection], teacher_id: str) -> BatchResult:
        size = len(injections)
        used = sum(item.size for item in self._queue)
        if used + size > self.limits.max_queue:
            return BatchResult(
                status="queue_full",
                http_status=429,
                message=(
                    f"backing queue is full ({used}/{self.limits.max_queue}); "
                    "try again when the pit is free"
                ),
                queued_count=used,
            )
        self._queue.append(
            _QueuedBatch(
                injections=list(injections),
                enqueued_at=time.time(),
                teacher_id=teacher_id,
                size=size,
            )
        )
        position = len(self._queue)
        reason = self.pit_status.reason or "busy"
        return BatchResult(
            status="queued",
            http_status=429,
            message=f"pit is busy ({reason}); queued at position {position}",
            queued_count=sum(item.size for item in self._queue),
            queue_position=position,
            items=[
                InjectResult(
                    status="queued",
                    http_status=429,
                    message=f"queued at position {position}",
                    queue_position=position,
                    queued_count=sum(item.size for item in self._queue),
                )
                for _ in injections
            ],
        )

    def drain(self) -> list[BatchResult]:
        """Process the backing queue now that the pit is free."""
        drained: list[BatchResult] = []
        with self._lock:
            if self.pit_status.busy:
                return drained
            pending = list(self._queue)
            self._queue.clear()
            if not pending:
                return drained
            outer = self._txn_depth == 0
            try:
                with self._mutation(f"drain batches={len(pending)}") as txn:
                    for batch in pending:
                        # Already admitted past the rate limit when it was queued.
                        drained.append(
                            self.inject_many(
                                batch.injections, skip_rate_limit=True, skip_busy=True
                            )
                        )
            except PersistenceError:
                # Put the work back; none of it was saved.
                self._queue[:0] = pending
                raise
            if self.store is not None and outer:
                for item in drained:
                    item.saved = True
                    item.store_generation = txn.generation
        return drained

    def pending_count(self) -> int:
        with self._lock:
            return sum(item.size for item in self._queue)

    def _accept_one(self, item: Injection) -> InjectResult:
        if not item.text or not item.text.strip():
            decision = self.gate.offer(
                item.text,
                item.quadrant,
                trusted=False,
                teacher_id=item.teacher_id,
            )
            return InjectResult(
                status="empty",
                http_status=400,
                message="empty concept text",
                decision=decision,
            )

        known = set(self.quadrant_map.ids)
        if known and item.quadrant not in known:
            return InjectResult(
                status="unknown_quadrant",
                http_status=400,
                message=f"unknown quadrant {item.quadrant!r}",
            )

        provenance = apply_trust_policy(item.teacher_id, bool(item.trusted), self.allowlist())
        granted = bool(provenance["trusted_granted"])
        key = item.concept_key or derive_concept_key(item.text)
        candidate = make_grain(
            item.text,
            item.quadrant,
            trusted=granted,
            source_name=item.source_name or f"teacher:{item.teacher_id}",
            teacher_id=item.teacher_id,
            concept_key=key,
            trust_provenance=provenance,
        )

        # A trusted source (allowlisted teacher) is how a later, higher
        # trusted grain breaks a tie. Do not add it as another disputed
        # party against untrusted grains; promote it and let the gate hook
        # close any matching open conflict. Two *trusted* teachers who
        # contradict each other are still staged and not auto-resolved.
        if granted:
            trusted_clash = self._find_trusted_clash(candidate)
            if trusted_clash is not None:
                record = self._stage_conflict(candidate, trusted_clash)
                return InjectResult(
                    status="conflict",
                    http_status=409,
                    message=(
                        "disagreement between trusted teachers; both grains "
                        "staged and the conflict is not auto-resolved"
                    ),
                    grain=candidate,
                    conflict_id=record["id"],
                    trust_provenance=provenance,
                )
        else:
            conflict = self._find_disagreement(candidate)
            if conflict is not None:
                record = self._stage_conflict(candidate, conflict)
                return InjectResult(
                    status="conflict",
                    http_status=409,
                    message=(
                        "disagreement with another teacher; both grains staged "
                        "and the conflict is not auto-resolved"
                    ),
                    grain=candidate,
                    conflict_id=record["id"],
                    trust_provenance=provenance,
                )

        # If this injection is trusted, consider_trusted_grain runs from
        # the gate hook after promotion and can close an open conflict.
        decision = self.gate.offer(
            item.text,
            item.quadrant,
            trusted=granted,
            source_name=item.source_name or f"teacher:{item.teacher_id}",
            teacher_id=item.teacher_id,
            concept_key=key,
            trust_provenance=provenance,
        )
        if decision.accepted:
            return InjectResult(
                status="promoted",
                http_status=200,
                message=decision.reason,
                grain=decision.grain,
                decision=decision,
                trust_provenance=provenance,
            )
        return InjectResult(
            status=decision.reason,
            http_status=200,
            message=decision.reason,
            grain=decision.grain,
            decision=decision,
            trust_provenance=provenance,
        )

    def _other_grains(self, excluding_id: str | None = None) -> list[Grain]:
        found: list[Grain] = []
        for grain in self.sandpit.all_grains(include_superseded=True):
            if excluding_id and grain.id == excluding_id:
                continue
            # Already-resolved (superseded) grains stay out of new disputes.
            if grain.status == "superseded":
                continue
            found.append(grain)
        # Also grains sitting only in an open conflict (may already be parked).
        seen = {grain.id for grain in found}
        for record in self._conflicts:
            if record.get("status") != "open":
                continue
            for payload in record.get("grains", []):
                if payload.get("id") in seen:
                    continue
                found.append(Grain.from_record(payload))
                seen.add(payload["id"])
        return found

    def _find_trusted_clash(self, candidate: Grain) -> Grain | None:
        """Another already-promoted trusted grain that contradicts this one."""
        for existing in self.sandpit.all_grains(include_superseded=False):
            if not existing.trusted:
                continue
            if existing.teacher_id and existing.teacher_id == candidate.teacher_id:
                continue
            if existing.id == candidate.id:
                continue
            if self._disagreement(candidate, existing):
                return existing
        return None

    def _find_disagreement(self, candidate: Grain) -> Grain | dict | None:
        """Return the first existing grain (or conflict) that disagrees."""
        for record in self._conflicts:
            if record.get("status") != "open":
                continue
            for payload in record.get("grains", []):
                existing = Grain.from_record(payload)
                if existing.teacher_id and existing.teacher_id == candidate.teacher_id:
                    continue
                if existing.id == candidate.id:
                    continue
                reason = self._disagreement(candidate, existing)
                if reason:
                    return record
        for existing in self.sandpit.all_grains(include_superseded=False):
            if existing.teacher_id and existing.teacher_id == candidate.teacher_id:
                continue
            if existing.id == candidate.id:
                continue
            reason = self._disagreement(candidate, existing)
            if reason:
                return existing
        return None

    def _disagreement(self, left: Grain, right: Grain) -> str | None:
        key_a = left.concept_key or derive_concept_key(left.text)
        key_b = right.concept_key or derive_concept_key(right.text)
        score = cosine(left.embedding, right.embedding)
        return contradiction_reason(
            left.text,
            right.text,
            key_a,
            key_b,
            score,
            concept_cosine=self.concept_cosine,
            low_agreement=self.low_agreement,
        )

    def _stage_conflict(self, candidate: Grain, other: Grain | dict) -> dict:
        """Stage both sides, write a conflict record, do not auto-resolve."""
        if isinstance(other, dict):
            record = other
            # Attach the new grain to the existing open conflict.
            self._stage_grain(candidate, record["id"], "teacher-conflict")
            if candidate.id not in {g.get("id") for g in record["grains"]}:
                record["grains"].append(candidate.record())
                record["reason"] = record.get("reason") or "teacher-conflict"
            self._save_conflicts()
            return record

        existing = other
        reason = self._disagreement(candidate, existing) or "teacher-conflict"
        # Park the already-promoted grain so neither side is live.
        if self.sandpit.get(existing.id) is not None and existing.id in {
            g.id for g in self.sandpit.all_grains(include_superseded=False)
        }:
            parked = self.sandpit.park(existing.id, reason="conflict")
            existing = parked
        self._stage_grain(existing, None, "teacher-conflict")
        record = {
            "id": uuid.uuid4().hex[:16],
            "status": "open",
            "concept_key": candidate.concept_key or existing.concept_key,
            "quadrant": candidate.quadrant,
            "reason": reason,
            "grains": [existing.record(), candidate.record()],
            "created_at": time.time(),
            "resolved_at": None,
            "resolved_by": None,
            "chosen_id": None,
        }
        self._stage_grain(candidate, record["id"], "teacher-conflict")
        # Fill conflict id on the first staging line too (rewritten below).
        self._conflicts.append(record)
        self._save_conflicts()
        self.gate.stage(
            {
                "reason": "teacher-conflict",
                "conflict_id": record["id"],
                "concept_key": record["concept_key"],
                "conflict_reason": reason,
                "grain_ids": [existing.id, candidate.id],
                "teacher_ids": [existing.teacher_id, candidate.teacher_id],
            }
        )
        return record

    def _stage_grain(self, grain: Grain, conflict_id: str | None, reason: str) -> None:
        payload = grain.record()
        payload["reason"] = reason
        payload["conflict_id"] = conflict_id
        self.gate.stage(payload)
        # If the candidate was never promoted, it only lives in staging
        # and the conflict file. That is deliberate.

    def _load_conflicts(self) -> None:
        self._conflicts = []
        if not self.conflicts_path.is_file():
            return
        for line in self.conflicts_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            self._conflicts.append(json.loads(line))

    def _save_conflicts(self) -> None:
        if self.store is not None:
            # Conflicts are committed with the rest of the change.
            return
        self.conflicts_path.parent.mkdir(parents=True, exist_ok=True)
        with self.conflicts_path.open("w", encoding="utf-8") as handle:
            for record in self._conflicts:
                handle.write(json.dumps(record) + "\n")

    def open_conflicts(self) -> list[dict]:
        return [dict(record) for record in self._conflicts if record.get("status") == "open"]

    def all_conflicts(self) -> list[dict]:
        return [dict(record) for record in self._conflicts]

    def resolve(
        self,
        conflict_id: str,
        chosen_id: str,
        resolved_by: str = "human",
    ) -> dict:
        """Human (or a later caller) chooses one grain. The other stays staged."""
        outer = self._txn_depth == 0
        with self._mutation(f"resolve conflict={conflict_id} by={resolved_by}") as txn:
            result = self._resolve_locked(conflict_id, chosen_id, resolved_by)
        if self.store is not None and outer:
            result["saved"] = True
            result["store_generation"] = txn.generation
        return result

    def _resolve_locked(self, conflict_id: str, chosen_id: str, resolved_by: str) -> dict:
        record = self._conflict_by_id(conflict_id)
        if record is None:
            raise KeyError(f"unknown conflict: {conflict_id}")
        if record.get("status") != "open":
            raise ValueError(f"conflict {conflict_id} is already {record.get('status')}")
        chosen = None
        for payload in record["grains"]:
            if payload["id"] == chosen_id:
                chosen = Grain.from_record(payload)
                break
        if chosen is None:
            raise KeyError(f"grain {chosen_id} is not part of conflict {conflict_id}")
        decision = None
        already_live = chosen.id in {
            g.id for g in self.sandpit.all_grains(include_superseded=False)
        }
        if already_live:
            decision = GateDecision(
                accepted=True,
                reason="already-promoted",
                grain=self.sandpit.get(chosen.id) or chosen,
                pending_checks=() if chosen.trusted else ("improves_later_retrieval",),
            )
        else:
            # Temporarily disable the promotion hook so resolve does
            # not re-enter consider_trusted_grain.
            hook = self.gate.on_promoted
            self.gate.on_promoted = None
            try:
                decision = self.gate.offer(
                    chosen.text,
                    chosen.quadrant,
                    grain_id=chosen.id,
                    trusted=bool(chosen.trusted),
                    source_name=chosen.source_name,
                    source_sha256=chosen.source_sha256,
                    teacher_id=chosen.teacher_id,
                    concept_key=chosen.concept_key,
                    trust_provenance=chosen.trust_provenance,
                )
            finally:
                self.gate.on_promoted = hook
            if not decision.accepted and decision.reason in {
                "near_duplicate",
                "exact-duplicate",
            }:
                # Near-duplicate of a parked self, or a trusted copy
                # that is already live. Restore the chosen grain.
                chosen.status = "active"
                chosen.superseded_by = None
                self.sandpit.promote(chosen)
                decision = GateDecision(
                    accepted=True,
                    reason="restored-from-conflict",
                    grain=chosen,
                    pending_checks=() if chosen.trusted else ("improves_later_retrieval",),
                )
            elif not decision.accepted:
                raise ValueError(f"gate rejected the chosen grain: {decision.reason}")
        record["status"] = "resolved"
        record["resolved_at"] = time.time()
        record["resolved_by"] = resolved_by
        record["chosen_id"] = chosen_id
        self._save_conflicts()
        return {
            "conflict": record,
            "decision_reason": decision.reason if decision is not None else None,
        }

    def consider_trusted_grain(self, grain: Grain) -> list[str]:
        """A trusted source arriving later may break open ties.

        Allowlisted-teacher grains and school-mode trusted grains both
        qualify. Untrusted grains never auto-resolve a conflict.
        """
        if not grain.trusted:
            return []
        resolved_ids: list[str] = []
        # Snapshot open conflicts; resolve() takes the lock itself.
        for record in list(self.open_conflicts()):
            if not self._conflict_matches_grain(record, grain):
                continue
            # If this grain is already one of the sides, choose it.
            chosen_id = grain.id
            known = {payload["id"] for payload in record["grains"]}
            if chosen_id not in known:
                # A new trusted source on this concept wins the tie.
                # Attach it to the record so the choice is recorded, and
                # it should already be promoted (we are in the gate hook).
                record["grains"].append(grain.record())
            try:
                self.resolve(record["id"], chosen_id, resolved_by=self._resolver_label(grain))
            except (KeyError, ValueError):
                # Already resolved, or the grain is not choosable because
                # it was just appended after resolve looked it up. Retry
                # after the append was saved.
                record["status"] = "resolved"
                record["resolved_at"] = time.time()
                record["resolved_by"] = self._resolver_label(grain)
                record["chosen_id"] = chosen_id
                self._save_conflicts()
            resolved_ids.append(record["id"])
        return resolved_ids

    def _resolver_label(self, grain: Grain) -> str:
        if grain.teacher_id:
            return f"trusted-teacher:{grain.teacher_id}"
        if grain.source_name:
            return f"school-mode:{grain.source_name}"
        return "trusted-source"

    def _conflict_matches_grain(self, record: dict, grain: Grain) -> bool:
        key = grain.concept_key or derive_concept_key(grain.text)
        if record.get("concept_key") and key and record["concept_key"] == key:
            return True
        probe = make_grain(
            grain.text,
            grain.quadrant,
            grain_id=grain.id,
            concept_key=key,
        )
        for payload in record.get("grains", []):
            existing = Grain.from_record(payload)
            if self._disagreement(probe, existing) or same_concept(
                probe, existing, self.concept_cosine
            ):
                return True
        return False

    def _conflict_by_id(self, conflict_id: str) -> dict | None:
        for record in self._conflicts:
            if record.get("id") == conflict_id:
                return record
        return None


    # ---- durable store ----

    @contextmanager
    def _mutation(self, op: str):
        """Run a change under the arm lock; with a store, commit it first.

        Nested calls (for example a trusted grain resolving a conflict from
        inside an inject) join the outer transaction. Only the outermost
        call commits. If the commit fails, the in-memory pit and conflicts
        are reloaded from the last committed state and PersistenceError is
        raised, so the caller can never report the change as saved.
        """
        with self._lock:
            if self.store is None:
                yield _Txn(op=op)
                return
            if self._txn_depth > 0:
                self._txn_depth += 1
                try:
                    yield self._txn
                finally:
                    self._txn_depth -= 1
                return
            committed_gen = self.store.begin()
            if committed_gen != self._generation:
                # Another process committed since we last loaded.
                self._reload_from_store()
            txn = _Txn(op=op)
            self._txn = txn
            self._txn_depth = 1
            try:
                yield txn
                self._commit(txn)
            except BaseException:
                self.store.rollback()
                self._reload_from_store()
                raise
            finally:
                self._txn_depth = 0
                self._txn = None

    def transaction(self, op: str = "manual"):
        """Public wrapper so the CLI can make sleep mode one durable change."""
        return self._mutation(op)

    def _buffer_staged(self, record: dict) -> bool:
        if self._txn is None:
            return False  # outside a transaction: plain JSONL append
        self._txn.staged.append(dict(record))
        return True

    def _commit(self, txn: _Txn) -> None:
        grains: list[tuple[dict, dict | None]] = []
        new_grains: dict[str, tuple[str, str, str | None]] = {}
        for grain in self.sandpit.all_grains(include_superseded=True):
            record = grain.record()
            sig = _grain_signature(record)
            previous = self._committed_grains.get(grain.id)
            if previous is None or previous[0] != sig:
                grains.append(
                    (
                        record,
                        None
                        if previous is None
                        else {"status": previous[1], "superseded_by": previous[2]},
                    )
                )
                new_grains[grain.id] = (sig, record["status"], record.get("superseded_by"))
        conflicts: list[tuple[dict, dict | None]] = []
        new_conflicts: dict[str, tuple[str, str]] = {}
        for record in self._conflicts:
            text = json.dumps(record, sort_keys=True, separators=(",", ":"))
            previous = self._committed_conflicts.get(record["id"])
            if previous is None or previous[0] != text:
                conflicts.append(
                    (json.loads(text), None if previous is None else {"status": previous[1]})
                )
                new_conflicts[record["id"]] = (text, str(record.get("status")))
        if not grains and not conflicts and not txn.staged:
            self.store.rollback()
            txn.generation = self._generation
            return
        generation = self.store.write_changes(txn.op, grains, conflicts, txn.staged)
        self._committed_grains.update(new_grains)
        self._committed_conflicts.update(new_conflicts)
        self._generation = generation
        txn.generation = generation
        # The JSONL near-miss file is a readable mirror; the store is the
        # record. Append only after the commit so it never shows unsaved work.
        for record in txn.staged:
            try:
                self.gate.append_staging(record)
            except OSError:
                pass

    def _reload_from_store(self) -> None:
        assert self.store is not None
        grain_records = self.store.load_grains()
        self.sandpit.replace_all(grain_records)
        self._committed_grains = {}
        for record in grain_records:
            grain = Grain.from_record(record)
            fresh = grain.record()
            self._committed_grains[grain.id] = (
                _grain_signature(fresh),
                fresh["status"],
                fresh.get("superseded_by"),
            )
        self._conflicts = self.store.load_conflicts()
        self._committed_conflicts = {
            record["id"]: (
                json.dumps(record, sort_keys=True, separators=(",", ":")),
                str(record.get("status")),
            )
            for record in self._conflicts
        }
        self._generation = self.store.generation()

    def checkpoint(self) -> int | None:
        """Commit anything changed outside a transaction (e.g. heat stats)."""
        if self.store is None:
            return None
        with self._mutation("checkpoint") as txn:
            pass
        return txn.generation


# ---- helpers used by the HTTP layer and the CLI ----

def parse_injections(payload: dict, default_teacher_id: str | None = None) -> list[Injection]:
    """Accept a single D-RAYL block or a batch of them."""
    teacher_id = payload.get("teacher_id") or default_teacher_id
    default_trusted = bool(payload.get("trusted", False))
    default_quadrant = payload.get("quadrant")

    raw_items: list[dict]
    if isinstance(payload.get("blocks"), list):
        raw_items = payload["blocks"]
    elif isinstance(payload.get("injections"), list):
        raw_items = payload["injections"]
    elif isinstance(payload.get("grains"), list):
        raw_items = payload["grains"]
    elif isinstance(payload.get("block"), dict):
        block = dict(payload["block"])
        # Lift request-level fields if the block omitted them.
        block.setdefault("teacher_id", teacher_id)
        block.setdefault("trusted", default_trusted)
        block.setdefault("quadrant", default_quadrant)
        raw_items = [block]
    else:
        raw_items = [payload]

    injections: list[Injection] = []
    for item in raw_items:
        if not isinstance(item, dict):
            raise ValueError("each block must be a JSON object")
        text = item.get("text") or item.get("concept") or item.get("concept_text") or ""
        quadrant = item.get("quadrant") or item.get("quadrant_tag") or default_quadrant
        tid = item.get("teacher_id") or teacher_id
        if not quadrant:
            raise ValueError("each injection needs a quadrant tag")
        if not tid:
            raise ValueError("each injection needs a teacher_id")
        tags = item.get("tags") or ()
        if isinstance(tags, str):
            tags = (tags,)
        injections.append(
            Injection(
                text=str(text),
                quadrant=str(quadrant),
                teacher_id=str(tid),
                trusted=bool(item.get("trusted", default_trusted)),
                concept_key=item.get("concept_key") or item.get("conceptKey"),
                tags=tuple(tags),
                block_id=str(item.get("id") or ""),
                source_name=item.get("source_name"),
            )
        )
    return injections

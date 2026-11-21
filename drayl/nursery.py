"""Nursery helpers: seed documents, school-mode load, and heat-map flood."""

from __future__ import annotations

import hashlib
import json
import random
import re
from dataclasses import dataclass, field
from pathlib import Path

from drayl.embeddings import tokenize
from drayl.gate import ComplianceGate, GateDecision
from drayl.memory import InMemoryMemory
from drayl.quadrants import QuadrantMap, load_quadrant_map
from drayl.router import Block, Router
from drayl.sandpit import InMemorySandPit

HEADING_RE = re.compile(r"^(#+)\s+(.*)$")
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")

DEFAULT_DATA = Path("data") / "sandpit.json"
DEFAULT_STAGING = Path("data") / "near-misses.jsonl"
DEFAULT_HEAT = Path("data") / "heat.json"
DEFAULT_FLOOD_SEED = 38


@dataclass
class SeedResult:
    path: Path
    source_name: str
    source_sha256: str
    trusted: bool
    decisions: list[GateDecision] = field(default_factory=list)

    @property
    def promoted(self) -> int:
        return sum(1 for decision in self.decisions if decision.accepted)

    @property
    def rejected(self) -> int:
        return sum(1 for decision in self.decisions if not decision.accepted)


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def chunk_document(text: str) -> list[str]:
    """Split a .txt/.md document into short grains.

    Headings are kept as context on the following paragraph. Short
    heading-only blocks are not stored on their own.
    """
    blocks = re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip())
    chunks: list[str] = []
    heading = ""
    for block in blocks:
        raw_lines = [line.rstrip() for line in block.splitlines() if line.strip()]
        if not raw_lines:
            continue
        match = HEADING_RE.match(raw_lines[0].strip())
        if match and len(raw_lines) == 1:
            heading = match.group(2).strip()
            continue
        body_lines = list(raw_lines)
        if match:
            heading = match.group(2).strip()
            body_lines = raw_lines[1:]
        body = "\n".join(body_lines).strip()
        if not body:
            continue
        piece = f"{heading}. {body}" if heading else body
        if len(piece) <= 400:
            chunks.append(piece)
        else:
            chunks.extend(_split_long(piece, heading))
    return chunks


def _split_long(text: str, heading: str) -> list[str]:
    sentences = [part.strip() for part in SENTENCE_RE.split(text) if part.strip()]
    if not sentences:
        return [text]
    out: list[str] = []
    buf = ""
    for sentence in sentences:
        candidate = f"{buf} {sentence}".strip() if buf else sentence
        if len(candidate) > 400 and buf:
            out.append(buf)
            buf = sentence
        else:
            buf = candidate
    if buf:
        out.append(buf)
    # If we stripped the heading off a mid-chunk, leave the text as-is.
    _ = heading
    return out


def assign_quadrant(text: str, quadrant_map: QuadrantMap) -> str:
    order, matched = quadrant_map.order_for((), tokens=tokenize(text))
    if matched:
        return order[0]
    return "misc"


def seed_paths(
    paths: list[str | Path],
    sandpit: InMemorySandPit,
    gate: ComplianceGate,
    quadrant_map: QuadrantMap,
    trusted: bool = False,
) -> list[SeedResult]:
    results: list[SeedResult] = []
    for path in paths:
        results.append(seed_path(path, sandpit, gate, quadrant_map, trusted=trusted))
    return results


def seed_path(
    path: str | Path,
    sandpit: InMemorySandPit,
    gate: ComplianceGate,
    quadrant_map: QuadrantMap,
    trusted: bool = False,
) -> SeedResult:
    source = Path(path)
    text = source.read_text(encoding="utf-8")
    checksum = file_sha256(source)
    source_name = str(source)
    result = SeedResult(
        path=source,
        source_name=source_name,
        source_sha256=checksum,
        trusted=trusted,
    )
    for chunk in chunk_document(text):
        quadrant = assign_quadrant(chunk, quadrant_map)
        decision = gate.offer(
            chunk,
            quadrant,
            trusted=trusted,
            source_name=source_name,
            source_sha256=checksum,
        )
        result.decisions.append(decision)
    return result


def school_path(
    path: str | Path,
    sandpit: InMemorySandPit,
    gate: ComplianceGate,
    quadrant_map: QuadrantMap,
) -> SeedResult:
    """One-step load of a fundamental document with trusted=True."""
    return seed_path(path, sandpit, gate, quadrant_map, trusted=True)


def synthetic_queries(sandpit: InMemorySandPit, count: int, rng_seed: int = DEFAULT_FLOOD_SEED) -> list[str]:
    grains = sorted(sandpit.all_grains(), key=lambda grain: grain.id)
    if not grains or count <= 0:
        return []
    rng = random.Random(rng_seed)
    templates = (
        "What is {snippet}?",
        "Tell me about {snippet}",
        "{snippet}",
        "Explain {snippet}",
    )
    queries: list[str] = []
    for index in range(count):
        grain = grains[index % len(grains)]
        words = tokenize(grain.text)[:8]
        if not words:
            continue
        # Deterministic shuffle of the word window for variety, using the loop index.
        window = list(words)
        if len(window) > 2:
            rotate = rng.randrange(len(window))
            window = window[rotate:] + window[:rotate]
        snippet = " ".join(window)
        template = templates[index % len(templates)]
        queries.append(template.format(snippet=snippet))
    return queries


# Groups used when flooding a school pack so maths, grammar and general
# each get sample queries when those quadrants have grains.
FLOOD_GROUPS = (
    ("numbers", ("numbers",)),
    ("grammar_speech", ("grammar_speech",)),
    ("facts", ("facts", "misc", "procedures", "personal")),
)


def synthetic_queries_by_quadrant(
    sandpit: InMemorySandPit,
    count: int,
    rng_seed: int = DEFAULT_FLOOD_SEED,
) -> list[str]:
    """Build queries from loaded content, spreading them across key quadrants."""
    if count <= 0:
        return []
    grouped: dict[str, list] = {name: [] for name, _ids in FLOOD_GROUPS}
    for grain in sandpit.all_grains():
        for name, ids in FLOOD_GROUPS:
            if grain.quadrant in ids:
                grouped[name].append(grain)
                break
    active = [name for name, _ids in FLOOD_GROUPS if grouped[name]]
    if not active:
        return synthetic_queries(sandpit, count, rng_seed=rng_seed)

    # Round-robin through groups that have grains so each gets a share.
    per_group: dict[str, int] = {name: 0 for name in active}
    for index in range(count):
        per_group[active[index % len(active)]] += 1

    queries: list[str] = []
    for offset, name in enumerate(active):
        n = per_group[name]
        if n == 0:
            continue
        # Temporary pit-like view: reuse synthetic_queries by feeding a shim.
        shim = InMemorySandPit()
        for grain in grouped[name]:
            shim.promote(grain)
        queries.extend(synthetic_queries(shim, n, rng_seed=rng_seed + offset))
    return queries[:count]


def flood(
    sandpit: InMemorySandPit,
    router: Router,
    count: int,
    rng_seed: int = DEFAULT_FLOOD_SEED,
    k: int = 3,
    by_quadrant: bool = True,
) -> list[str]:
    if by_quadrant:
        queries = synthetic_queries_by_quadrant(sandpit, count, rng_seed=rng_seed)
    else:
        queries = synthetic_queries(sandpit, count, rng_seed=rng_seed)
    for query in queries:
        result = router.dispatch(Block(text=query), k=k)
        helped = [grain.quadrant for grain in result.grains]
        if result.memory.context:
            context = result.memory.context
        elif result.grains:
            context = result.grains[0].text
        else:
            context = "no stored grain matched"
        router.memory.remember(query, context, helped_quadrants=helped)
    return queries


def write_heat(sandpit: InMemorySandPit, path: str | Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "grain_count": len(sandpit.all_grains()),
        "total_hits": sum(grain.hits for grain in sandpit.all_grains()),
        "heat": sandpit.heat_map(),
    }
    destination.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return destination


def load_context(
    data_path: str | Path,
    staging_path: str | Path,
    config_path: str | Path | None = None,
) -> tuple[InMemorySandPit, InMemoryMemory, ComplianceGate, QuadrantMap, Router]:
    data = Path(data_path)
    if data.exists():
        sandpit = InMemorySandPit.load(data)
    else:
        sandpit = InMemorySandPit()
    quadrant_map = load_quadrant_map(config_path)
    memory = InMemoryMemory(default_quadrants=list(quadrant_map.ids))
    gate = ComplianceGate(sandpit, staging_path=staging_path)
    router = Router(quadrant_map, sandpit, memory, gate=gate)
    return sandpit, memory, gate, quadrant_map, router

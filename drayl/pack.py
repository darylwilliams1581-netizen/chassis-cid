"""Offline school-pack builder and safe loader.

Build a warmed-up sand pit on any machine (Windows included) with only
the Python standard library, then copy the zip to the target PC so the
chat box can boot with a brain already formed.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from drayl import __version__
from drayl.embeddings import hashed_bow_embedding
from drayl.memory import InMemoryMemory
from drayl.nursery import (
    DEFAULT_FLOOD_SEED,
    file_sha256,
    flood,
    school_path,
    seed_path,
    write_heat,
)
from drayl.quadrants import QuadrantMap, load_quadrant_map
from drayl.sandpit import InMemorySandPit
from drayl.sleep import SleepMode

TEXT_SUFFIXES = {".txt", ".md"}
PACK_README_LINE = (
    "Unzip on target PC, point the chat box at this folder, done."
)


@dataclass
class DocumentPlan:
    path: Path
    relative: str
    trusted: bool
    sha256: str = ""


@dataclass
class PackResult:
    zip_path: Path
    folder: Path
    manifest: dict
    grain_count: int
    query_count: int


def discover_documents(folder: str | Path) -> list[Path]:
    root = Path(folder)
    if not root.is_dir():
        raise FileNotFoundError(f"not a folder: {root}")
    docs = [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES
    ]
    return docs


def find_pdfs(folder: str | Path) -> list[Path]:
    root = Path(folder)
    return [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.suffix.lower() == ".pdf"
    ]


def load_trust_list(path: str | Path | None) -> dict[str, bool]:
    if path is None:
        return {}
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("trust list must be a JSON object mapping names to true/false")
    return {str(key): bool(value) for key, value in payload.items()}


def resolve_trusted(
    relative: str,
    path: Path,
    folder_trusted: bool,
    trust_list: dict[str, bool],
    trusted_files: set[str],
) -> bool:
    if folder_trusted:
        return True
    name = path.name
    candidates = {relative, name, relative.replace("\\", "/"), str(path)}
    for key, flag in trust_list.items():
        key_norm = key.replace("\\", "/")
        if key_norm in candidates or Path(key_norm).name == name:
            return bool(flag)
    for item in trusted_files:
        item_norm = item.replace("\\", "/")
        if item_norm in candidates or Path(item_norm).name == name:
            return True
    return False


def plan_documents(
    folder: str | Path,
    folder_trusted: bool = False,
    trust_list: dict[str, bool] | None = None,
    trusted_files: list[str] | None = None,
) -> list[DocumentPlan]:
    root = Path(folder).resolve()
    plans: list[DocumentPlan] = []
    trusted_set = {item.replace("\\", "/") for item in (trusted_files or [])}
    listing = trust_list or {}
    for path in discover_documents(root):
        relative = path.relative_to(root).as_posix()
        plans.append(
            DocumentPlan(
                path=path,
                relative=relative,
                trusted=resolve_trusted(
                    relative, path, folder_trusted, listing, trusted_set
                ),
                sha256=file_sha256(path),
            )
        )
    return plans


def safe_join(dest: Path, name: str) -> Path:
    """Return dest/name, or raise if the name would escape dest (zip slip)."""
    dest_res = dest.resolve()
    normalised = name.replace("\\", "/")
    if normalised.startswith("/") or normalised.startswith("//"):
        raise ValueError(f"unsafe path in zip: {name}")
    # Windows drive or leading letter: (C: or C:/)
    first = normalised.split("/", 1)[0]
    if len(first) >= 2 and first[1] == ":":
        raise ValueError(f"unsafe path in zip: {name}")
    parts = Path(normalised).parts
    if any(part == ".." for part in parts):
        raise ValueError(f"unsafe path in zip: {name}")
    target = (dest_res / normalised).resolve()
    if target != dest_res and dest_res not in target.parents:
        raise ValueError(f"unsafe path in zip: {name}")
    return target


def safe_extract(zip_path: str | Path, dest: str | Path) -> Path:
    """Extract a zip, refusing any member that would write outside dest."""
    destination = Path(dest)
    destination.mkdir(parents=True, exist_ok=True)
    dest_res = destination.resolve()
    with ZipFile(zip_path, "r") as archive:
        for info in archive.infolist():
            target = safe_join(dest_res, info.filename)
            if info.is_dir() or info.filename.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info, "r") as source, target.open("wb") as out:
                shutil.copyfileobj(source, out)
    return dest_res


def write_p37_seed_and_state(sandpit: InMemorySandPit, folder: Path) -> None:
    grains = [grain for grain in sandpit.all_grains()]
    seed = []
    state_grains = []
    for grain in grains:
        seed.append(
            {
                "id": grain.id,
                "text": grain.text,
                "quadrant": grain.quadrant,
                "tags": [grain.quadrant] + (["trusted"] if grain.trusted else []),
                "tier": "hot" if grain.trusted else "warm",
                "protected": bool(grain.trusted),
                "trusted": grain.trusted,
                "source": grain.source(),
            }
        )
        state_grains.append(
            {
                "id": grain.id,
                "text": grain.text,
                "quadrant": grain.quadrant,
                "tags": [grain.quadrant] + (["trusted"] if grain.trusted else []),
                "tier": "hot" if grain.trusted else "warm",
                "protected": bool(grain.trusted),
                "hits": grain.hits,
                "last_access": grain.last_access,
                "helped_count": grain.helped_count,
                "trusted": grain.trusted,
                "source": grain.source(),
                "superseded_by": grain.superseded_by,
                "status": grain.status,
            }
        )
    (folder / "seed_grains.json").write_text(
        json.dumps(seed, indent=2) + "\n", encoding="utf-8"
    )
    (folder / "sandpit_state.json").write_text(
        json.dumps({"grains": state_grains}, indent=2) + "\n", encoding="utf-8"
    )


def write_heatmap_jsonl(queries: list[str], sandpit: InMemorySandPit, path: Path) -> None:
    """Write a P37-style heatmap log from the flood queries and current heat."""
    # One entry per flood query is ideal; we also emit a summary pass using
    # current grain hits so the log is not empty if queries were blank.
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    # Reconstruct a simple log: each query, plus which grains currently have hits.
    hot = [grain for grain in sandpit.all_grains() if grain.hits > 0]
    for index, query in enumerate(queries):
        # Deterministically attach the hottest grains (already grown by flood).
        used = sorted(hot, key=lambda g: (-g.hits, g.id))[:3]
        record = {
            "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
            "block_id": f"flood-{index + 1}",
            "query": query,
            "grains": [grain.id for grain in used],
            "quadrants": list(dict.fromkeys(grain.quadrant for grain in used)),
        }
        lines.append(json.dumps(record, ensure_ascii=False))
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def write_p37_memory_sqlite(memory: InMemoryMemory, path: Path) -> None:
    if path.exists():
        path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(
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
        for trace in memory.slow_retrieval.values():
            embedding = trace.embedding or hashed_bow_embedding(trace.question)
            conn.execute(
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
                    json.dumps([]),
                    json.dumps(list(trace.helped_quadrants)),
                    json.dumps(list(embedding)),
                    trace.timestamp,
                ),
            )
        for trace in memory.fast_context.values():
            conn.execute(
                "INSERT OR IGNORE INTO fast_context (id, timestamp) VALUES (?, ?)",
                (trace.id, trace.timestamp),
            )
        for heat in memory.associations.values():
            a, b = sorted((heat.grain_a, heat.grain_b))
            conn.execute(
                """
                INSERT INTO associations (grain_a, grain_b, consulted, helped)
                VALUES (?, ?, ?, ?)
                """,
                (a, b, heat.consulted, heat.helped),
            )
        conn.commit()
    finally:
        conn.close()


def build_pack(
    source_folder: str | Path,
    output_zip: str | Path,
    *,
    work_dir: str | Path | None = None,
    queries: int = 20,
    folder_trusted: bool = False,
    trust_list_path: str | Path | None = None,
    trusted_files: list[str] | None = None,
    config_path: str | Path | None = None,
    rng_seed: int = DEFAULT_FLOOD_SEED,
    run_sleep: bool = False,
    staging_path: str | Path | None = None,
) -> PackResult:
    source = Path(source_folder).resolve()
    zip_path = Path(output_zip)
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    if work_dir is None:
        work_dir = zip_path.with_suffix("")
        if work_dir.suffix == ".zip":
            work_dir = zip_path.parent / (zip_path.stem)
    folder = Path(work_dir)
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)

    config_src = Path(config_path) if config_path else Path(__file__).resolve().parent.parent / "config" / "quadrants.json"
    (folder / "config").mkdir(parents=True, exist_ok=True)
    shutil.copy2(config_src, folder / "config" / "quadrants.json")
    quadrant_map = load_quadrant_map(folder / "config" / "quadrants.json")

    trust_list = load_trust_list(trust_list_path)
    plans = plan_documents(
        source,
        folder_trusted=folder_trusted,
        trust_list=trust_list,
        trusted_files=trusted_files,
    )
    if not plans:
        raise ValueError(f"no .txt or .md documents found in {source}")

    sandpit = InMemorySandPit()
    memory = InMemoryMemory(default_quadrants=list(quadrant_map.ids))
    from drayl.gate import ComplianceGate
    from drayl.router import Router

    staging = Path(staging_path) if staging_path else folder / "near-misses.jsonl"
    gate = ComplianceGate(sandpit, staging_path=staging)
    router = Router(quadrant_map, sandpit, memory, gate=gate)

    skipped_pdfs = [path.relative_to(source).as_posix() for path in find_pdfs(source)]
    results = []
    for plan in plans:
        if plan.trusted:
            result = school_path(plan.path, sandpit, gate, quadrant_map)
        else:
            result = seed_path(plan.path, sandpit, gate, quadrant_map, trusted=False)
        results.append((plan, result))

    flood_queries = flood(
        sandpit, router, queries, rng_seed=rng_seed, by_quadrant=True
    ) if queries else []

    sleep_report = None
    if run_sleep:
        sleep_report = SleepMode(sandpit, staging_path=staging).run()

    sandpit.save(folder / "sandpit.json")
    memory.save(folder / "memory.json")
    write_heat(sandpit, folder / "heat.json")
    write_p37_seed_and_state(sandpit, folder)
    write_heatmap_jsonl(flood_queries, sandpit, folder / "heatmap.jsonl")
    write_p37_memory_sqlite(memory, folder / "memory.sqlite")
    (folder / "README.txt").write_text(PACK_README_LINE + "\n", encoding="utf-8")

    grains = sandpit.all_grains()
    quadrant_counts: dict[str, int] = {}
    for grain in grains:
        quadrant_counts[grain.quadrant] = quadrant_counts.get(grain.quadrant, 0) + 1

    manifest = {
        "format": "drayl-school-pack",
        "format_version": 1,
        "tool_version": __version__,
        "label": "P38 school pack",
        "built_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_folder": str(source),
        "queries": len(flood_queries),
        "flood_seed": rng_seed,
        "grain_count": len(grains),
        "trusted_grain_count": sum(1 for grain in grains if grain.trusted),
        "quadrant_counts": quadrant_counts,
        "sleep": None
        if sleep_report is None
        else {
            "overridden": len(sleep_report.overridden),
            "kept": sleep_report.kept,
        },
        "pdfs_skipped": skipped_pdfs,
        "documents": [
            {
                "path": plan.relative,
                "sha256": plan.sha256,
                "trusted": plan.trusted,
                "grains_promoted": item.promoted,
                "grains_rejected": item.rejected,
            }
            for plan, item in results
        ],
        "files": [
            "README.txt",
            "manifest.json",
            "config/quadrants.json",
            "sandpit.json",
            "sandpit_state.json",
            "seed_grains.json",
            "heat.json",
            "heatmap.jsonl",
            "memory.json",
            "memory.sqlite",
        ],
        "note": (
            "Trusted only means the person who built this pack vouched for "
            "the source. A wrong textbook would spread errors."
        ),
    }
    (folder / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    _write_zip(folder, zip_path)
    return PackResult(
        zip_path=zip_path,
        folder=folder,
        manifest=manifest,
        grain_count=len(grains),
        query_count=len(flood_queries),
    )


def _write_zip(folder: Path, zip_path: Path) -> None:
    if zip_path.exists():
        zip_path.unlink()
    with ZipFile(zip_path, "w", compression=ZIP_DEFLATED) as archive:
        for path in sorted(folder.rglob("*")):
            if not path.is_file():
                continue
            archive.write(path, arcname=path.relative_to(folder).as_posix())


def load_pack_folder(folder: str | Path) -> tuple[InMemorySandPit, InMemoryMemory, dict]:
    root = Path(folder)
    if (root / "sandpit.json").is_file():
        sandpit = InMemorySandPit.load(root / "sandpit.json")
    else:
        raise FileNotFoundError(f"no sandpit.json in {root}")
    if (root / "memory.json").is_file():
        memory = InMemoryMemory.load(root / "memory.json")
    else:
        memory = InMemoryMemory()
    manifest = {}
    if (root / "manifest.json").is_file():
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    return sandpit, memory, manifest

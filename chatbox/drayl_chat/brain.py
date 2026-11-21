"""Load a P38 school pack as the chat box brain.

This copy of the P37 chat box can be pointed at a school-pack folder
(or zip) with --brain PATH or the DRAYL_BRAIN environment variable.

    python -m drayl_chat --brain school-pack-folder
    python -m drayl_chat --brain school-pack.zip

Zip members are extracted with a path-traversal guard. Nothing is
fetched from the network.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from zipfile import ZipFile


def safe_join(dest: Path, name: str) -> Path:
    dest_res = dest.resolve()
    normalised = name.replace("\\", "/")
    if normalised.startswith("/") or normalised.startswith("//"):
        raise ValueError(f"unsafe path in zip: {name}")
    first = normalised.split("/", 1)[0]
    if len(first) >= 2 and first[1] == ":":
        raise ValueError(f"unsafe path in zip: {name}")
    if any(part == ".." for part in Path(normalised).parts):
        raise ValueError(f"unsafe path in zip: {name}")
    target = (dest_res / normalised).resolve()
    if target != dest_res and dest_res not in target.parents:
        raise ValueError(f"unsafe path in zip: {name}")
    return target


def safe_extract(zip_path: str | Path, dest: str | Path) -> Path:
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


def resolve_brain(brain: str | Path) -> Path:
    """Return the pack folder, extracting a zip if needed."""
    path = Path(brain).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"school pack not found: {path}")
    if path.is_dir():
        return path.resolve()
    if path.is_file() and path.suffix.lower() == ".zip":
        dest = path.with_name(path.stem + "-unpacked")
        if dest.exists() and (dest / "manifest.json").is_file():
            return dest.resolve()
        if dest.exists():
            shutil.rmtree(dest)
        return safe_extract(path, dest)
    raise ValueError(f"school pack must be a folder or a .zip file: {path}")


def pack_paths(folder: str | Path) -> dict[str, Path | None]:
    root = Path(folder)
    config = root / "config" / "quadrants.json"
    if not config.is_file():
        alt = root / "quadrants.json"
        config = alt if alt.is_file() else config
    seed = root / "seed_grains.json"
    return {
        "root": root,
        "config": config if config.is_file() else None,
        "seed": seed if seed.is_file() else None,
        "state": root / "sandpit_state.json",
        "memory": root / "memory.sqlite",
        "heatmap": root / "heatmap.jsonl",
        "manifest": root / "manifest.json",
    }


def read_manifest(folder: str | Path) -> dict:
    path = Path(folder) / "manifest.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))

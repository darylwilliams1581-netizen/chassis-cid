"""Offline school-pack builder, zip safety, and trusted-flag round trip."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

from drayl.pack import (
    PACK_README_LINE,
    build_pack,
    load_pack_folder,
    safe_extract,
    safe_join,
)
from drayl.sandpit import InMemorySandPit

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"
MATHS = SAMPLES / "year9-maths-sample.md"


def test_pack_trusted_sample_floods_and_survives_zip_round_trip(tmp_path):
    source = tmp_path / "docs"
    source.mkdir()
    (source / "tiny.md").write_text(MATHS.read_text(encoding="utf-8"), encoding="utf-8")
    zip_path = tmp_path / "pack.zip"
    work = tmp_path / "built"
    result = build_pack(
        source,
        zip_path,
        work_dir=work,
        queries=5,
        folder_trusted=True,
        rng_seed=38,
    )
    assert result.query_count == 5
    assert result.grain_count >= 3
    assert result.manifest["trusted_grain_count"] == result.grain_count
    assert result.manifest["documents"][0]["trusted"] is True
    assert (work / "README.txt").read_text(encoding="utf-8").strip() == PACK_README_LINE
    heat = json.loads((work / "heat.json").read_text(encoding="utf-8"))
    assert heat["total_hits"] > 0
    assert heat["heat"], "heat map should have grain entries after a flood of 5"

    extracted = tmp_path / "extracted"
    safe_extract(zip_path, extracted)
    reloaded, memory, manifest = load_pack_folder(extracted)
    assert manifest["trusted_grain_count"] == result.grain_count
    trusted = reloaded.query(trusted=True)
    assert trusted
    assert all(grain.trusted for grain in trusted)
    # Flags and source checksums survive the zip.
    original = InMemorySandPit.load(work / "sandpit.json")
    for grain in original.all_grains():
        found = reloaded.get(grain.id)
        assert found is not None
        assert found.trusted is True
        assert found.source_sha256 == grain.source_sha256
        assert found.text == grain.text
    assert (extracted / "heatmap.jsonl").read_text(encoding="utf-8").strip()
    assert memory.slow_retrieval, "memory store should be in the pack"
    assert (extracted / "memory.sqlite").is_file()
    assert (extracted / "config" / "quadrants.json").is_file()


def test_trust_list_and_trusted_file_flags(tmp_path, quadrant_map):
    source = tmp_path / "docs"
    source.mkdir()
    (source / "trusted.md").write_text("Pythagoras: a squared plus b squared equals c squared.\n", encoding="utf-8")
    (source / "notes.md").write_text("A leftover note about someone adding two plus two.\n", encoding="utf-8")
    trust_list = tmp_path / "trust.json"
    trust_list.write_text(json.dumps({"trusted.md": True}), encoding="utf-8")
    result = build_pack(
        source,
        tmp_path / "pack.zip",
        work_dir=tmp_path / "built",
        queries=0,
        trust_list_path=trust_list,
    )
    docs = {item["path"]: item["trusted"] for item in result.manifest["documents"]}
    assert docs["trusted.md"] is True
    assert docs["notes.md"] is False
    pit, _memory, _manifest = load_pack_folder(tmp_path / "built")
    flags = {grain.text: grain.trusted for grain in pit.all_grains()}
    assert any(trusted and "pythagoras" in text.casefold() for text, trusted in flags.items())
    assert any((not trusted) and "two plus two" in text.casefold() for text, trusted in flags.items())


def test_safe_extract_rejects_path_traversal(tmp_path):
    zip_path = tmp_path / "evil.zip"
    with ZipFile(zip_path, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("../escape.txt", "nope")
    dest = tmp_path / "out"
    dest.mkdir()
    try:
        safe_extract(zip_path, dest)
        raise AssertionError("path traversal should have been rejected")
    except ValueError as exc:
        assert "unsafe path" in str(exc)
    assert not (tmp_path / "escape.txt").exists()
    assert list(dest.rglob("*")) == [] or all(p.is_dir() for p in dest.rglob("*"))


def test_safe_join_rejects_absolute_and_parent_paths(tmp_path):
    dest = tmp_path / "out"
    dest.mkdir()
    try:
        safe_join(dest, "/tmp/evil")
        raise AssertionError("expected failure")
    except ValueError:
        pass
    try:
        safe_join(dest, "..\\windows\\evil.txt")
        raise AssertionError("expected failure")
    except ValueError:
        pass


def test_pack_cli_with_five_queries(tmp_path):
    import subprocess

    source = tmp_path / "docs"
    source.mkdir()
    (source / "tiny.md").write_text(MATHS.read_text(encoding="utf-8"), encoding="utf-8")
    zip_path = tmp_path / "cli-pack.zip"
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "nursery.py"),
            "pack",
            str(source),
            "--trusted",
            "--queries",
            "5",
            "--output",
            str(zip_path),
            "--work-dir",
            str(tmp_path / "work"),
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
    assert zip_path.is_file()
    assert "flood queries: 5" in completed.stdout

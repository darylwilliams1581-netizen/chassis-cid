"""Nursery script: school-mode load, seed, and flood."""

from __future__ import annotations

import sys
from pathlib import Path

from drayl.gate import ComplianceGate
from drayl.memory import InMemoryMemory
from drayl.nursery import flood, school_path, seed_path
from drayl.quadrants import load_quadrant_map
from drayl.router import Router
from drayl.sandpit import InMemorySandPit

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "samples" / "year9-maths-sample.md"
NURSERY = ROOT / "scripts" / "nursery.py"


def test_school_function_loads_sample_with_trusted_flag(tmp_path, quadrant_map):
    sandpit = InMemorySandPit()
    gate = ComplianceGate(sandpit, staging_path=tmp_path / "near-misses.jsonl")
    result = school_path(SAMPLE, sandpit, gate, quadrant_map)
    assert result.trusted is True
    assert result.promoted >= 3
    grains = sandpit.query(trusted=True)
    assert grains
    assert all(grain.trusted for grain in grains)
    assert all(grain.source_sha256 == result.source_sha256 for grain in grains)
    assert any("pythagoras" in grain.text.casefold() or "hypotenuse" in grain.text.casefold() for grain in grains)
    # Most maths chunks should land in the numbers quadrant.
    assert any(grain.quadrant == "numbers" for grain in grains)


def test_seed_function_leaves_grains_untrusted(tmp_path, quadrant_map):
    sandpit = InMemorySandPit()
    gate = ComplianceGate(sandpit, staging_path=tmp_path / "near-misses.jsonl")
    result = seed_path(SAMPLE, sandpit, gate, quadrant_map, trusted=False)
    assert result.trusted is False
    assert result.promoted >= 1
    assert all(not grain.trusted for grain in sandpit.all_grains())


def test_flood_grows_heat_counts(tmp_path, quadrant_map):
    sandpit = InMemorySandPit()
    gate = ComplianceGate(sandpit, staging_path=tmp_path / "near-misses.jsonl")
    school_path(SAMPLE, sandpit, gate, quadrant_map)
    memory = InMemoryMemory(default_quadrants=list(quadrant_map.ids))
    router = Router(quadrant_map, sandpit, memory, gate=gate)
    before = {grain.id: grain.hits for grain in sandpit.all_grains()}
    queries = flood(sandpit, router, 8, rng_seed=38)
    assert len(queries) == 8
    grown = [grain for grain in sandpit.all_grains() if grain.hits > before.get(grain.id, 0)]
    assert grown, "flood should raise hit counts on at least one grain"
    assert memory.slow_retrieval, "flood should write memory traces"


def test_school_cli_loads_sample_with_the_flag_set(tmp_path):
    import subprocess

    data = tmp_path / "sandpit.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(NURSERY),
            "--data",
            str(data),
            "--staging",
            str(tmp_path / "near-misses.jsonl"),
            "school",
            str(SAMPLE),
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
    assert "trusted: true" in completed.stdout
    assert data.is_file()
    loaded = InMemorySandPit.load(data)
    assert loaded.query(trusted=True)
    assert all(grain.trusted for grain in loaded.all_grains())

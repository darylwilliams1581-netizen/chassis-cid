"""Shared fixtures for the P36 base-layer tests."""

from __future__ import annotations

import pytest

from drayl.gate import ComplianceGate
from drayl.memory import InMemoryMemory
from drayl.quadrants import load_quadrant_map
from drayl.router import Router
from drayl.sandpit import InMemorySandPit


@pytest.fixture
def quadrant_map():
    return load_quadrant_map()


@pytest.fixture
def sandpit():
    return InMemorySandPit()


@pytest.fixture
def memory(quadrant_map):
    return InMemoryMemory(default_quadrants=list(quadrant_map.ids))


@pytest.fixture
def gate(sandpit, tmp_path):
    return ComplianceGate(sandpit, staging_path=tmp_path / "near-misses.jsonl")


@pytest.fixture
def router(quadrant_map, sandpit, memory, gate):
    return Router(quadrant_map, sandpit, memory, gate=gate)

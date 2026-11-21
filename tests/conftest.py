"""Shared fixtures. Each test gets its own data directory."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from drayl_chat.app import ChatBox  # noqa: E402
from drayl_chat.encoder import encode  # noqa: E402
from drayl_chat.memory import SqliteMemory  # noqa: E402
from drayl_chat.quadrants import load_quadrant_map  # noqa: E402
from drayl_chat.router import Router  # noqa: E402
from drayl_chat.sandpit import SandPit  # noqa: E402


@pytest.fixture
def root() -> Path:
    return ROOT


@pytest.fixture
def quadrant_map():
    return load_quadrant_map(ROOT / "config" / "quadrants.json")


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    path = tmp_path / "data"
    path.mkdir()
    (path / "staging").mkdir()
    return path


@pytest.fixture
def sandpit(data_dir: Path, root: Path) -> SandPit:
    pit = SandPit(state_path=data_dir / "sandpit_state.json")
    pit.load_seed(root / "config" / "seed_grains.json")
    return pit


@pytest.fixture
def memory(data_dir: Path) -> SqliteMemory:
    store = SqliteMemory(data_dir / "memory.sqlite")
    yield store
    store.close()


@pytest.fixture
def router(quadrant_map, sandpit, memory) -> Router:
    r = Router(quadrant_map, sandpit, memory)
    yield r
    r.close()


@pytest.fixture
def box(data_dir: Path) -> ChatBox:
    chat = ChatBox(data_dir=data_dir)
    yield chat
    chat.close()


@pytest.fixture
def make_block(quadrant_map):
    def _make(text: str):
        return encode(text, quadrant_map)

    return _make

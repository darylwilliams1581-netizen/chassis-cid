"""Dispatch really runs the sand pit and memory at the same time."""

from __future__ import annotations

import time

from drayl_chat.memory import SqliteMemory
from drayl_chat.router import Router
from drayl_chat.sandpit import SandPit

ARM_DELAY = 0.30
# Sequential would be about 0.60s. Parallel should sit just over one delay.
PARALLEL_CEILING = 0.50


def test_dispatch_runs_both_arms_in_parallel(quadrant_map, data_dir, root, make_block):
    sandpit = SandPit(delay=ARM_DELAY, state_path=data_dir / "sandpit_state.json")
    sandpit.load_seed(root / "config" / "seed_grains.json")
    memory = SqliteMemory(data_dir / "memory.sqlite", delay=ARM_DELAY)
    memory.remember("hello", "a previous greeting mattered", helped_quadrants=["misc"])
    router = Router(quadrant_map, sandpit, memory)
    try:
        started = time.perf_counter()
        result = router.dispatch(make_block("hello"))
        elapsed = time.perf_counter() - started
    finally:
        router.close()
        memory.close()

    assert elapsed >= ARM_DELAY, f"arms finished too quickly ({elapsed:.3f}s)"
    assert elapsed < PARALLEL_CEILING, (
        f"dispatch took {elapsed:.3f}s; expected under {PARALLEL_CEILING}s "
        f"if both {ARM_DELAY}s arms ran in parallel (sequential would be "
        f"about {ARM_DELAY * 2:.2f}s)"
    )
    assert result.memory.context == "a previous greeting mattered"
    assert result.quadrant_order  # bias peek also ran


def test_bias_order_is_not_delayed(quadrant_map, data_dir, make_block):
    memory = SqliteMemory(data_dir / "memory.sqlite", delay=ARM_DELAY)
    memory.remember(
        "what is two plus two",
        "the numbers quadrant helped",
        helped_quadrants=["numbers"],
    )
    default = list(quadrant_map.ids)
    started = time.perf_counter()
    order = memory.bias_order("what is two plus two", default)
    elapsed = time.perf_counter() - started
    memory.close()
    assert elapsed < 0.15, f"bias peek should be cheap, took {elapsed:.3f}s"
    assert order[0] == "numbers"

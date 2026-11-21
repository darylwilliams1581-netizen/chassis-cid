"""Dispatch really runs the sand pit and memory at the same time."""

from __future__ import annotations

import time

from drayl.memory import InMemoryMemory
from drayl.router import Block, Router
from drayl.sandpit import InMemorySandPit

ARM_DELAY = 0.30
# Sequential would be about 0.60s. Parallel should sit just over one delay.
PARALLEL_CEILING = 0.50


def test_dispatch_runs_both_arms_in_parallel(quadrant_map):
    sandpit = InMemorySandPit(delay=ARM_DELAY)
    memory = InMemoryMemory(delay=ARM_DELAY, default_quadrants=list(quadrant_map.ids))
    memory.remember("hello mate", "a previous greeting mattered", ["misc"])
    router = Router(quadrant_map, sandpit, memory)

    started = time.perf_counter()
    result = router.dispatch(Block(text="hello mate"), k=3)
    elapsed = time.perf_counter() - started

    assert elapsed >= ARM_DELAY, f"arms finished too quickly ({elapsed:.3f}s)"
    assert elapsed < PARALLEL_CEILING, (
        f"dispatch took {elapsed:.3f}s; expected under {PARALLEL_CEILING}s "
        f"if both {ARM_DELAY}s arms ran in parallel (sequential would be "
        f"about {ARM_DELAY * 2:.2f}s)"
    )
    # Both arms actually produced a result, not just finished quickly.
    assert result.memory.context == "a previous greeting mattered"
    assert result.quadrant_order  # bias peek also ran

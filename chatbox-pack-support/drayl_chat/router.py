"""Thin router: the only module that talks to the base layer.

It matches a D-RAYL block to capability tags, peeks at memory for a
quadrant bias, then fires the sand pit and the memory arm at the same
time and merges the two results.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from drayl_chat.encoder import Block
from drayl_chat.memory import SqliteMemory
from drayl_chat.merge import MergedResult, merge
from drayl_chat.quadrants import QuadrantMap
from drayl_chat.sandpit import SandPit


class Router:
    """Thin dispatcher sitting above the base layer."""

    def __init__(
        self,
        quadrant_map: QuadrantMap,
        sandpit: SandPit,
        memory: SqliteMemory,
        executor: ThreadPoolExecutor | None = None,
    ):
        self.quadrant_map = quadrant_map
        self.sandpit = sandpit
        self.memory = memory
        self._own_executor = executor is None
        self._executor = executor or ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="drayl"
        )

    def close(self) -> None:
        if self._own_executor:
            self._executor.shutdown(wait=False)

    def dispatch(self, block: Block, k: int = 5) -> MergedResult:
        default_order, matched_tags = self.quadrant_map.order_for(
            block.capability_tags, tokens=block.tokens
        )
        # Cheap peek at the fast context store so memory can bias which
        # quadrant the sand pit searches first. The full recall still
        # runs in parallel with the sand search.
        quadrant_order = self.memory.bias_order(block.text, default_order)

        sand_future = self._executor.submit(
            self.sandpit.search, block.embedding, quadrant_order, k
        )
        memory_future = self._executor.submit(self.memory.recall, block.text)
        sand = sand_future.result()
        memory = memory_future.result()

        return merge(
            block,
            sand,
            memory,
            quadrant_order=quadrant_order,
            matched_tags=matched_tags,
        )

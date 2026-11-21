"""Thin router: the only program that knows the base layer exists.

It matches incoming blocks to capability tags, peeks at memory for a
quadrant bias, then fires the sand pit and the memory arm at the same
time and merges the two results.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from drayl.embeddings import hashed_bow_embedding, tokenize
from drayl.gate import ComplianceGate, GateDecision
from drayl.memory import Memory, MemoryResult
from drayl.quadrants import QuadrantMap, load_quadrant_map
from drayl.sandpit import Grain, SandPit


@dataclass
class Block:
    """A D-RAYL block as the router sees it.

    The encoder (not in this scaffold) is what will turn raw input into
    blocks. For now a block is just text plus optional capability tags.
    """

    text: str
    tags: tuple[str, ...] = ()
    id: str = ""


@dataclass
class MergedResult:
    """Merged dispatch result.

    Grains are raw content from the sand pit. Memory is context about
    what mattered last time a similar question was asked.
    """

    grains: list[Grain]
    memory: MemoryResult
    quadrant_order: list[str]
    matched_tags: list[str] = field(default_factory=list)


class Router:
    """Thin dispatcher sitting above the base layer."""

    def __init__(
        self,
        quadrant_map: QuadrantMap,
        sandpit: SandPit,
        memory: Memory,
        gate: ComplianceGate | None = None,
    ):
        self.quadrant_map = quadrant_map
        self.sandpit = sandpit
        self.memory = memory
        self.gate = gate

    @classmethod
    def from_config(
        cls,
        sandpit: SandPit,
        memory: Memory,
        config_path: str | None = None,
        gate: ComplianceGate | None = None,
    ) -> "Router":
        return cls(load_quadrant_map(config_path), sandpit, memory, gate=gate)

    def dispatch(self, block: Block, k: int = 5) -> MergedResult:
        default_order, matched_tags = self.quadrant_map.order_for(
            block.tags, tokens=tokenize(block.text)
        )
        # Cheap peek at the fast context store so memory can bias which
        # quadrant the sand pit searches first. The full recall still
        # runs in parallel with the sand search.
        quadrant_order = self.memory.bias_order(block.text, default_order)
        query_vec = hashed_bow_embedding(block.text)

        with ThreadPoolExecutor(max_workers=2) as pool:
            sand_future = pool.submit(self.sandpit.search, query_vec, quadrant_order, k)
            memory_future = pool.submit(self.memory.recall, block.text)
            grains = sand_future.result()
            memory = memory_future.result()

        return MergedResult(
            grains=grains,
            memory=memory,
            quadrant_order=quadrant_order,
            matched_tags=matched_tags,
        )

    def write_back(self, text: str, quadrant: str, grain_id: str | None = None) -> GateDecision:
        """Offer a decoder-emitted chunk to the compliance gate."""
        if self.gate is None:
            raise RuntimeError("this router has no compliance gate")
        return self.gate.offer(text, quadrant, grain_id=grain_id)

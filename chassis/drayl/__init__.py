"""D-RAYL base layer scaffold (P36).

The base layer is the sand pit, the memory arm, the compliance gate,
and the static quadrant map. The router is the thin program above
them: it is the only program that talks to the base layer.
"""

from drayl.embeddings import cosine, hashed_bow_embedding
from drayl.gate import ComplianceGate, GateDecision
from drayl.memory import InMemoryMemory, Memory, MemoryResult, MemoryTrace
from drayl.quadrants import QuadrantMap, load_quadrant_map
from drayl.router import Block, MergedResult, Router
from drayl.sandpit import Grain, InMemorySandPit, SandPit
from drayl.stubs import DualMapReconciler, FrequencyRoutedWeights, SleepMode

__all__ = [
    "Block",
    "ComplianceGate",
    "DualMapReconciler",
    "FrequencyRoutedWeights",
    "GateDecision",
    "Grain",
    "InMemoryMemory",
    "InMemorySandPit",
    "Memory",
    "MemoryResult",
    "MemoryTrace",
    "MergedResult",
    "QuadrantMap",
    "Router",
    "SandPit",
    "SleepMode",
    "cosine",
    "hashed_bow_embedding",
    "load_quadrant_map",
]

__version__ = "0.36.0"

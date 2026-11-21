"""D-RAYL base layer with school mode (P38).

The base layer is the sand pit, the memory arm, the compliance gate,
the static quadrant map, and a minimal sleep-mode pass for trusted
sources. The router is the thin program above them: it is the only
program that talks to the base layer.
"""

from drayl.embeddings import cosine, hashed_bow_embedding
from drayl.gate import ComplianceGate, GateDecision
from drayl.memory import InMemoryMemory, Memory, MemoryResult, MemoryTrace
from drayl.merge import DEFAULT_TRUST_MARGIN, prefer_trusted
from drayl.quadrants import QuadrantMap, load_quadrant_map
from drayl.router import Block, MergedResult, Router
from drayl.sandpit import Grain, InMemorySandPit, SandPit
from drayl.sleep import SleepMode, SleepReport
from drayl.stubs import DualMapReconciler, FrequencyRoutedWeights

__all__ = [
    "Block",
    "ComplianceGate",
    "DEFAULT_TRUST_MARGIN",
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
    "SleepReport",
    "cosine",
    "hashed_bow_embedding",
    "load_quadrant_map",
    "prefer_trusted",
]

__version__ = "0.38.0"

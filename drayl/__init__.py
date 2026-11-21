"""D-RAYL base layer with school mode and a teacher arm (P39).

The base layer is the sand pit, the memory arm, the compliance gate,
the static quadrant map, and a minimal sleep-mode pass for trusted
sources. The router is the thin program above them: it is the only
program that talks to the base layer.

The teacher arm sits beside the router. External teachers call in on
localhost; this package never calls out to an AI.
"""

from drayl.embeddings import cosine, hashed_bow_embedding
from drayl.gate import ComplianceGate, GateDecision
from drayl.memory import InMemoryMemory, Memory, MemoryResult, MemoryTrace
from drayl.merge import DEFAULT_TRUST_MARGIN, prefer_trusted
from drayl.pit_status import PitStatus
from drayl.quadrants import QuadrantMap, load_quadrant_map
from drayl.router import Block, MergedResult, Router
from drayl.sandpit import Grain, InMemorySandPit, SandPit
from drayl.sleep import SleepMode, SleepReport
from drayl.stubs import DualMapReconciler, FrequencyRoutedWeights
from drayl.teacher import InjectResult, Injection, TeacherArm, TeacherLimits
from drayl.teacher_server import BindError, check_bind_host, ensure_token

__all__ = [
    "BindError",
    "Block",
    "ComplianceGate",
    "DEFAULT_TRUST_MARGIN",
    "DualMapReconciler",
    "FrequencyRoutedWeights",
    "GateDecision",
    "Grain",
    "InMemoryMemory",
    "InMemorySandPit",
    "InjectResult",
    "Injection",
    "Memory",
    "MemoryResult",
    "MemoryTrace",
    "MergedResult",
    "PitStatus",
    "QuadrantMap",
    "Router",
    "SandPit",
    "SleepMode",
    "SleepReport",
    "TeacherArm",
    "TeacherLimits",
    "check_bind_host",
    "cosine",
    "ensure_token",
    "hashed_bow_embedding",
    "load_quadrant_map",
    "prefer_trusted",
]

__version__ = "0.39.0"

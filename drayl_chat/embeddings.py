"""Deterministic hashed bag-of-words vectors and D-RAYL symbol ids.

No model download, no network. The same text always gives the same
vector and the same symbol ids on any machine.
"""

from __future__ import annotations

import hashlib
import math
import re

TOKEN_RE = re.compile(r"[a-z0-9]+")
DEFAULT_DIM = 64
SYMBOL_SPACE = 65_000


def tokenize(text: str) -> list[str]:
    return TOKEN_RE.findall(text.lower())


def symbol_id(token: str) -> int:
    """Map a token into the fixed 65,000-symbol D-RAYL space."""
    digest = hashlib.sha256(f"drayl-symbol:{token}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "little") % SYMBOL_SPACE


def symbols_for(tokens: list[str]) -> list[int]:
    return [symbol_id(token) for token in tokens]


def hashed_bow_embedding(text: str, dim: int = DEFAULT_DIM) -> tuple[float, ...]:
    """Turn text into a fixed-length vector.

    Each token is hashed to a bucket (and a sign) with SHA-256, so the
    same text always gives the same vector on any machine.
    """
    vec = [0.0] * dim
    for token in tokenize(text):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "little") % dim
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vec[index] += sign
    norm = math.sqrt(sum(x * x for x in vec))
    if norm > 0.0:
        vec = [x / norm for x in vec]
    return tuple(vec)


def cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    """Cosine similarity. Zero if either vector has no length."""
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)

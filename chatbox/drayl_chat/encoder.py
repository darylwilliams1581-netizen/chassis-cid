"""Encoder: incoming text becomes a D-RAYL block before the router sees it."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from drayl_chat.embeddings import hashed_bow_embedding, symbols_for, tokenize
from drayl_chat.quadrants import QuadrantMap


@dataclass
class Block:
    """A D-RAYL block: text, tokens, capability tags, and symbol ids."""

    id: str
    text: str
    tokens: list[str]
    capability_tags: list[str]
    symbol_ids: list[int]
    embedding: tuple[float, ...] = field(repr=False)


def encode(text: str, quadrant_map: QuadrantMap, block_id: str | None = None) -> Block:
    cleaned = text.strip()
    tokens = tokenize(cleaned)
    tags = quadrant_map.tags_for_tokens(tokens)
    return Block(
        id=block_id or str(uuid.uuid4()),
        text=cleaned,
        tokens=tokens,
        capability_tags=tags,
        symbol_ids=symbols_for(tokens),
        embedding=hashed_bow_embedding(cleaned),
    )

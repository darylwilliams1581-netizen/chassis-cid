"""Decoder: merged results become D-RAYL symbols and readable reply text.

In later versions this also catches infer's raw numeric output. In v1
the infer stub already returns text, and the decoder assigns symbols
and packages the reply for the interface.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from drayl_chat.embeddings import SYMBOL_SPACE, symbols_for, tokenize
from drayl_chat.merge import MergedResult


@dataclass
class DecodedReply:
    text: str
    tokens: list[str]
    symbol_ids: list[int]
    symbol_space: int = SYMBOL_SPACE
    stub_notice: str = ""
    used_grain_ids: list[str] = field(default_factory=list)
    used_quadrants: list[str] = field(default_factory=list)


def decode(text: str, merged: MergedResult, stub_notice: str = "") -> DecodedReply:
    tokens = tokenize(text)
    return DecodedReply(
        text=text,
        tokens=tokens,
        symbol_ids=symbols_for(tokens),
        stub_notice=stub_notice,
        used_grain_ids=[hit.grain_id for hit in merged.hits],
        used_quadrants=list(merged.used_quadrants),
    )

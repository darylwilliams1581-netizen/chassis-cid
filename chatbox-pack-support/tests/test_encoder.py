"""Encoder produces tokens, tags, and symbol ids in the 65,000 space."""

from __future__ import annotations

from drayl_chat.embeddings import SYMBOL_SPACE, symbol_id
from drayl_chat.encoder import encode


def test_encode_block_fields(quadrant_map):
    block = encode("What is two plus two?", quadrant_map)
    assert block.text == "What is two plus two?"
    assert "two" in block.tokens
    assert "plus" in block.capability_tags or "two" in block.tokens
    assert block.symbol_ids
    assert all(0 <= sid < SYMBOL_SPACE for sid in block.symbol_ids)
    assert len(block.embedding) == 64
    assert block.id


def test_symbol_ids_are_deterministic():
    assert symbol_id("hello") == symbol_id("hello")
    assert 0 <= symbol_id("hello") < SYMBOL_SPACE


def test_numbers_tag_maps_to_numbers_quadrant(quadrant_map):
    block = encode("please calculate two plus two", quadrant_map)
    order, matched = quadrant_map.order_for(block.capability_tags, block.tokens)
    assert "numbers" in order
    assert order[0] == "numbers" or "numbers" in quadrant_map.match_tags(block.capability_tags)
    assert matched

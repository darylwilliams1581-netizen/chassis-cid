"""Memory keeps a fast store, a slower store, and association heat."""

from __future__ import annotations

from drayl.memory import InMemoryMemory


def test_remember_lands_in_both_stores():
    memory = InMemoryMemory(fast_limit=8)
    trace = memory.remember("hello", "a greeting", ["misc"])
    assert trace.id in memory.fast_context
    assert trace.id in memory.slow_retrieval


def test_fast_store_evicts_oldest_but_slow_keeps_it():
    memory = InMemoryMemory(fast_limit=2)
    first = memory.remember("question one", "first mattered", ["facts"])
    memory.remember("question two", "second mattered", ["facts"])
    memory.remember("question three", "third mattered", ["facts"])
    assert first.id not in memory.fast_context
    assert first.id in memory.slow_retrieval
    recalled = memory.recall("question one")
    assert recalled.context == "first mattered"
    assert recalled.source == "slow"


def test_association_heat_increments():
    memory = InMemoryMemory()
    first = memory.associate("grain-a", "grain-b")
    assert first.consulted == 1
    assert first.helped == 0
    again = memory.associate("grain-b", "grain-a", helped=True)
    assert again is first
    assert again.consulted == 2
    assert again.helped == 1

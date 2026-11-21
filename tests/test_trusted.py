"""School mode: trusted metadata, query, and gate promotion."""

from __future__ import annotations

import json

from drayl.sandpit import InMemorySandPit


def test_grain_record_shows_trusted_and_source(sandpit):
    grain = sandpit.add(
        "a squared plus b squared equals c squared",
        quadrant="numbers",
        grain_id="pythagoras-trusted",
        trusted=True,
        source_name="samples/year9-maths-sample.md",
        source_sha256="abc123",
    )
    record = grain.record()
    assert record["trusted"] is True
    assert record["source"] == {
        "name": "samples/year9-maths-sample.md",
        "sha256": "abc123",
    }
    assert record["quadrant"] == "numbers"
    assert "embedding" in record
    assert "hits" in record
    # The same record must be JSON-exportable and loadable.
    exported = json.loads(json.dumps(record))
    assert exported["trusted"] is True
    assert exported["source"]["sha256"] == "abc123"


def test_query_filters_by_trusted_quadrant_and_source(sandpit):
    sandpit.add("two plus two is four", "numbers", grain_id="u1", trusted=False)
    sandpit.add(
        "three plus three is six",
        "numbers",
        grain_id="t1",
        trusted=True,
        source_name="year9-maths-sample.md",
        source_sha256="def456",
    )
    sandpit.add(
        "Paris is the capital of France",
        "facts",
        grain_id="t2",
        trusted=True,
        source_name="other.md",
        source_sha256="aaa",
    )

    trusted = sandpit.query(trusted=True)
    assert {g.id for g in trusted} == {"t1", "t2"}
    assert sandpit.query(trusted=False)[0].id == "u1"
    assert [g.id for g in sandpit.query(trusted=True, quadrant="numbers")] == ["t1"]
    by_name = sandpit.query(source="year9-maths-sample.md")
    assert [g.id for g in by_name] == ["t1"]
    by_hash = sandpit.query(source="def456")
    assert [g.id for g in by_hash] == ["t1"]


def test_trusted_promotion_skips_proving(gate, sandpit):
    decision = gate.offer(
        "The product of two negative numbers is positive.",
        quadrant="numbers",
        trusted=True,
        source_name="year9-maths-sample.md",
        source_sha256="abc",
    )
    assert decision.accepted is True
    assert decision.reason == "trusted-source"
    assert decision.pending_checks == ()
    stored = sandpit.get(decision.grain.id)
    assert stored is not None
    assert stored.trusted is True
    assert stored.source_name == "year9-maths-sample.md"
    assert stored.source_sha256 == "abc"


def test_trusted_empty_is_rejected(gate, sandpit):
    decision = gate.offer("   ", quadrant="numbers", trusted=True)
    assert decision.accepted is False
    assert decision.reason == "empty"
    assert sandpit.all_grains() == []


def test_trusted_exact_duplicate_of_trusted_is_rejected(gate, sandpit, tmp_path):
    first = gate.offer(
        "A right-angled triangle has one angle of 90 degrees.",
        quadrant="numbers",
        trusted=True,
        source_name="doc-a.md",
        source_sha256="111",
    )
    assert first.accepted is True
    second = gate.offer(
        "A right-angled triangle has one angle of 90 degrees.",
        quadrant="numbers",
        trusted=True,
        source_name="doc-b.md",
        source_sha256="222",
    )
    assert second.accepted is False
    assert second.reason == "exact-duplicate"
    assert second.nearest_id == first.grain.id
    assert len(sandpit.query(trusted=True)) == 1
    records = [
        json.loads(line)
        for line in (tmp_path / "near-misses.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert records[0]["reason"] == "exact-duplicate"


def test_trusted_promotes_even_when_similar_untrusted_exists(gate, sandpit):
    untrusted = gate.offer(
        "The square of the hypotenuse equals the sum of the squares of the other two sides.",
        quadrant="numbers",
        trusted=False,
    )
    assert untrusted.accepted is True
    trusted = gate.offer(
        "The square of the hypotenuse equals the sum of the squares of the other two sides.",
        quadrant="numbers",
        trusted=True,
        source_name="year9-maths-sample.md",
        source_sha256="abc",
    )
    assert trusted.accepted is True
    assert trusted.reason == "trusted-source"
    assert trusted.pending_checks == ()
    assert sandpit.get(untrusted.grain.id) is not None
    assert sandpit.get(trusted.grain.id) is not None


def test_save_and_load_preserves_trusted_metadata(sandpit, tmp_path):
    sandpit.add(
        "Work out brackets first.",
        "numbers",
        grain_id="bidmas",
        trusted=True,
        source_name="year9.md",
        source_sha256="fff",
    )
    path = tmp_path / "sandpit.json"
    sandpit.save(path)
    loaded = InMemorySandPit.load(path)
    found = loaded.query(trusted=True, source="fff")
    assert len(found) == 1
    assert found[0].id == "bidmas"
    assert found[0].source_name == "year9.md"
    assert found[0].text.startswith("Work out brackets")

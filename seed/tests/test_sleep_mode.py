"""Trusted grains override untrusted near-duplicates in the same quadrant."""

from __future__ import annotations

import json

from drayl.sleep import SleepMode


TEXT = "a squared plus b squared equals c squared"


def test_sleep_trusted_overrides_untrusted_in_the_same_quadrant(sandpit, tmp_path):
    untrusted = sandpit.add(TEXT, "numbers", grain_id="untrusted-pythagoras", trusted=False)
    trusted = sandpit.add(
        TEXT,
        "numbers",
        grain_id="trusted-pythagoras",
        trusted=True,
        source_name="year9-maths-sample.md",
        source_sha256="abc",
    )
    staging = tmp_path / "near-misses.jsonl"
    report = SleepMode(sandpit, staging_path=staging).run()

    assert len(report.overridden) == 1
    change = report.overridden[0]
    assert change.grain_id == untrusted.id
    assert change.superseded_by == trusted.id
    assert change.quadrant == "numbers"

    assert untrusted.id not in {g.id for g in sandpit.all_grains()}
    kept = sandpit.get(untrusted.id)
    assert kept is not None
    assert kept.status == "superseded"
    assert kept.superseded_by == trusted.id
    # Trusted grain stays active.
    assert sandpit.get(trusted.id).status == "active"
    assert sandpit.get(trusted.id).trusted is True

    staged = [json.loads(line) for line in staging.read_text(encoding="utf-8").splitlines()]
    assert staged[0]["id"] == untrusted.id
    assert staged[0]["superseded_by"] == trusted.id
    assert staged[0]["reason"] == "superseded-by-trusted"
    assert staged[0]["text"] == TEXT


def test_sleep_does_not_override_across_quadrants(sandpit, tmp_path):
    trusted = sandpit.add(TEXT, "numbers", grain_id="trusted-numbers", trusted=True)
    untrusted = sandpit.add(TEXT, "facts", grain_id="untrusted-facts", trusted=False)
    report = SleepMode(sandpit, staging_path=tmp_path / "near-misses.jsonl").run()
    assert report.overridden == []
    assert {g.id for g in sandpit.all_grains()} == {trusted.id, untrusted.id}
    assert sandpit.get(untrusted.id).status == "active"


def test_sleep_untrusted_never_overrides_trusted(sandpit, tmp_path):
    trusted = sandpit.add(TEXT, "numbers", grain_id="trusted-pythagoras", trusted=True)
    sandpit.add(TEXT, "numbers", grain_id="untrusted-pythagoras", trusted=False)
    SleepMode(sandpit, staging_path=tmp_path / "near-misses.jsonl").run()
    assert sandpit.get(trusted.id).status == "active"
    assert sandpit.get(trusted.id).trusted is True
    assert sandpit.get(trusted.id).superseded_by is None
    # The untrusted one was the one that moved, not the trusted one.
    assert sandpit.get("untrusted-pythagoras").status == "superseded"

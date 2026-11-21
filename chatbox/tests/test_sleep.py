"""Sleep mode promotes/demotes by access counts and protects marked grains."""

from __future__ import annotations

from drayl_chat.heatmap import HeatMap
from drayl_chat.sandpit import make_grain
from drayl_chat.sleep import run_sleep


def test_sleep_noops_without_queries(sandpit, tmp_path):
    heat = HeatMap(tmp_path / "heatmap.jsonl")
    report = run_sleep(sandpit, heat)
    assert report.ran is False
    assert "nothing to reconcile" in report.message.lower() or "No queries" in report.message
    assert all(grain.tier == "warm" for grain in sandpit.all_grains())


def test_sleep_promote_demote_and_protect(data_dir, sandpit):
    # Fresh copies of grains with known tiers and protection flags.
    sandpit._grains.clear()
    sandpit.add(make_grain("hot one used a lot", "facts", "g-hot", tier="warm"))
    sandpit.add(make_grain("unused and unprotected", "facts", "g-cold", tier="warm"))
    sandpit.add(make_grain("unused but important", "facts", "g-safe", tier="warm", protected=True))
    sandpit.add(make_grain("used a little", "facts", "g-warm", tier="warm"))

    heat = HeatMap(data_dir / "heatmap.jsonl")
    for _ in range(3):
        heat.log("q-hot", ["g-hot"], ["facts"])
    heat.log("q-warm", ["g-warm"], ["facts"])
    # One more query that does not touch g-cold or g-safe, so their count stays 0.
    heat.log("q-other", ["g-hot"], ["facts"])

    report = run_sleep(sandpit, heat, hot_threshold=3)
    assert report.ran is True
    assert "g-hot" in report.promoted
    assert "g-cold" in report.demoted
    assert "g-safe" in report.protected_kept
    assert sandpit.get("g-hot").tier == "hot"
    assert sandpit.get("g-cold").tier == "cold"
    assert sandpit.get("g-safe").tier == "warm"
    assert sandpit.get("g-warm").tier == "warm"

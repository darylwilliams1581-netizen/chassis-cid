"""P39 teacher arm: injection, trust, conflicts, throttle, and localhost bind."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from drayl.gate import ComplianceGate
from drayl.pit_status import PitStatus
from drayl.quadrants import load_quadrant_map
from drayl.sandpit import InMemorySandPit, make_grain
from drayl.sleep import SleepMode
from drayl.teacher import (
    REASON_ALLOWLISTED,
    REASON_NOT_GRANTED,
    Injection,
    TeacherArm,
    TeacherLimits,
    contradiction_reason,
    derive_concept_key,
)
from drayl.teacher_server import (
    BindError,
    check_bind_host,
    ensure_token,
    serve_forever,
)


def _arm(tmp_path: Path, allowlist=None, limits=None, pit_status=None) -> TeacherArm:
    sandpit = InMemorySandPit()
    gate = ComplianceGate(sandpit, staging_path=tmp_path / "near-misses.jsonl")
    return TeacherArm(
        sandpit=sandpit,
        gate=gate,
        quadrant_map=load_quadrant_map(),
        allowlist=allowlist if allowlist is not None else [],
        limits=limits or TeacherLimits(),
        pit_status=pit_status or PitStatus(),
        conflicts_path=tmp_path / "conflicts.jsonl",
    )


def test_direct_injection_promotes_through_the_gate(tmp_path):
    arm = _arm(tmp_path)
    result = arm.inject(
        text="A year nine right-angled triangle has one angle of 90 degrees.",
        quadrant="numbers",
        teacher_id="grok",
    )
    assert result.status == "promoted"
    assert result.http_status == 200
    assert result.decision is not None and result.decision.accepted is True
    stored = arm.sandpit.get(result.grain.id)
    assert stored is not None
    assert stored.teacher_id == "grok"
    assert stored.trusted is False
    assert stored.text.startswith("A year nine")


def test_gate_still_rejects_empty_teacher_injection(tmp_path):
    arm = _arm(tmp_path)
    result = arm.inject(text="   ", quadrant="numbers", teacher_id="grok")
    assert result.status == "empty"
    assert result.http_status == 400
    assert result.decision is not None
    assert result.decision.reason == "empty"
    assert arm.sandpit.all_grains() == []


def test_gate_still_stages_near_duplicates(tmp_path):
    arm = _arm(tmp_path)
    first = arm.inject(
        text="The sky is blue on a clear day",
        quadrant="facts",
        teacher_id="grok",
    )
    assert first.status == "promoted"
    second = arm.inject(
        text="The sky is blue on a clear day",
        quadrant="facts",
        teacher_id="grok",
    )
    # Same teacher, same text: the gate stages a near-duplicate.
    # This is not a multi-teacher conflict.
    assert second.status == "near_duplicate"
    assert second.decision is not None
    assert second.decision.accepted is False
    assert len(arm.sandpit.all_grains()) == 1
    assert arm.open_conflicts() == []


def test_trusted_flag_granted_only_for_allowlisted_teacher(tmp_path):
    arm = _arm(tmp_path, allowlist=["codex"])
    granted = arm.inject(
        text="Brackets are worked out first in BIDMAS.",
        quadrant="numbers",
        teacher_id="codex",
        trusted=True,
    )
    assert granted.status == "promoted"
    assert granted.grain.trusted is True
    assert granted.trust_provenance["trusted_requested"] is True
    assert granted.trust_provenance["trusted_granted"] is True
    assert granted.trust_provenance["reason"] == REASON_ALLOWLISTED
    assert granted.trust_provenance["teacher_id"] == "codex"
    stored = arm.sandpit.get(granted.grain.id)
    assert stored.trusted is True
    assert stored.trust_provenance["reason"] == REASON_ALLOWLISTED


def test_trusted_flag_downgraded_if_not_allowlisted(tmp_path):
    arm = _arm(tmp_path, allowlist=["codex"])
    result = arm.inject(
        text="A verb is a doing word.",
        quadrant="grammar_speech",
        teacher_id="chatgpt",
        trusted=True,
    )
    assert result.status == "promoted"
    assert result.grain.trusted is False
    assert result.trust_provenance["trusted_requested"] is True
    assert result.trust_provenance["trusted_granted"] is False
    assert result.trust_provenance["reason"] == REASON_NOT_GRANTED
    stored = arm.sandpit.get(result.grain.id)
    assert stored.trusted is False
    assert stored.teacher_id == "chatgpt"
    record = stored.record()
    assert record["teacher_id"] == "chatgpt"
    assert record["trust_provenance"]["reason"] == "trust requested, not granted"
    assert record["trusted"] is False


def test_grain_record_keeps_teacher_id_and_provenance_after_reload(tmp_path):
    arm = _arm(tmp_path, allowlist=["grok"])
    result = arm.inject(
        text="The hypotenuse is the longest side of a right-angled triangle.",
        quadrant="numbers",
        teacher_id="grok",
        trusted=True,
        concept_key="hypotenuse longest side",
    )
    path = tmp_path / "sandpit.json"
    arm.sandpit.save(path)
    loaded = InMemorySandPit.load(path)
    found = loaded.get(result.grain.id)
    assert found is not None
    assert found.teacher_id == "grok"
    assert found.concept_key == "hypotenuse longest side"
    assert found.trust_provenance["reason"] == REASON_ALLOWLISTED
    assert found.trusted is True


def test_multi_teacher_conflict_stages_both_and_is_not_resolved(tmp_path):
    arm = _arm(tmp_path)
    first = arm.inject(
        text="The product of two negative numbers is negative.",
        quadrant="numbers",
        teacher_id="grok",
        concept_key="product of two negative numbers",
    )
    assert first.status == "promoted"
    first_id = first.grain.id
    assert arm.sandpit.get(first_id) is not None

    second = arm.inject(
        text="The product of two negative numbers is positive.",
        quadrant="numbers",
        teacher_id="chatgpt",
        concept_key="product of two negative numbers",
    )
    assert second.status == "conflict"
    assert second.http_status == 409
    assert second.conflict_id is not None

    # Neither grain stays live. Both are staged. The conflict is open.
    active_ids = {g.id for g in arm.sandpit.all_grains(include_superseded=False)}
    assert first_id not in active_ids
    assert second.grain.id not in active_ids
    parked = arm.sandpit.get(first_id)
    assert parked is not None
    assert parked.status == "conflict"

    conflicts = arm.open_conflicts()
    assert len(conflicts) == 1
    record = conflicts[0]
    assert record["id"] == second.conflict_id
    assert record["status"] == "open"
    grain_ids = {item["id"] for item in record["grains"]}
    assert grain_ids == {first_id, second.grain.id}
    teacher_ids = {item.get("teacher_id") for item in record["grains"]}
    assert teacher_ids == {"grok", "chatgpt"}

    staging = (tmp_path / "near-misses.jsonl").read_text(encoding="utf-8")
    assert "teacher-conflict" in staging
    # Still open: no auto-resolve.
    assert arm.open_conflicts()[0]["resolved_by"] is None
    assert arm.open_conflicts()[0]["status"] == "open"


def test_conflict_on_differing_numbers(tmp_path):
    arm = _arm(tmp_path)
    arm.inject(
        text="Water boils at 100 degrees Celsius at sea level.",
        quadrant="facts",
        teacher_id="grok",
        concept_key="water boiling point",
    )
    second = arm.inject(
        text="Water boils at 90 degrees Celsius at sea level.",
        quadrant="facts",
        teacher_id="gemini",
        concept_key="water boiling point",
    )
    assert second.status == "conflict"
    assert second.conflict_id is not None
    assert arm.open_conflicts()[0]["reason"] == "differing_numbers"


def test_conflict_on_negation(tmp_path):
    arm = _arm(tmp_path)
    arm.inject(
        text="A sentence fragment is a complete sentence.",
        quadrant="grammar_speech",
        teacher_id="copilot",
        concept_key="sentence fragment complete",
    )
    second = arm.inject(
        text="A sentence fragment is not a complete sentence.",
        quadrant="grammar_speech",
        teacher_id="grok",
        concept_key="sentence fragment complete",
    )
    assert second.status == "conflict"
    assert arm.open_conflicts()[0]["reason"] == "negation"


def test_human_resolve_chooses_one_grain(tmp_path):
    arm = _arm(tmp_path)
    first = arm.inject(
        text="The product of two negative numbers is negative.",
        quadrant="numbers",
        teacher_id="grok",
        concept_key="product of two negative numbers",
    )
    second = arm.inject(
        text="The product of two negative numbers is positive.",
        quadrant="numbers",
        teacher_id="chatgpt",
        concept_key="product of two negative numbers",
    )
    conflict_id = second.conflict_id
    arm.resolve(conflict_id, second.grain.id, resolved_by="human")
    assert arm.open_conflicts() == []
    resolved = [c for c in arm.all_conflicts() if c["id"] == conflict_id][0]
    assert resolved["status"] == "resolved"
    assert resolved["resolved_by"] == "human"
    assert resolved["chosen_id"] == second.grain.id
    # The chosen grain is live again. The other stays out of the active pit.
    active = {g.id for g in arm.sandpit.all_grains(include_superseded=False)}
    assert second.grain.id in active
    assert first.grain.id not in active


def test_allowlisted_teacher_trusted_grain_resolves_conflict(tmp_path):
    arm = _arm(tmp_path, allowlist=["codex"])
    arm.inject(
        text="The product of two negative numbers is negative.",
        quadrant="numbers",
        teacher_id="grok",
        concept_key="product of two negative numbers",
    )
    arm.inject(
        text="The product of two negative numbers is positive.",
        quadrant="numbers",
        teacher_id="chatgpt",
        concept_key="product of two negative numbers",
    )
    assert arm.open_conflicts()
    later = arm.inject(
        text="The product of two negative numbers is positive.",
        quadrant="numbers",
        teacher_id="codex",
        trusted=True,
        concept_key="product of two negative numbers",
    )
    assert later.status == "promoted"
    assert later.grain.trusted is True
    assert arm.open_conflicts() == []
    resolved = arm.all_conflicts()[0]
    assert resolved["status"] == "resolved"
    assert resolved["resolved_by"].startswith("trusted-teacher:codex")
    assert later.grain.id in {
        g.id for g in arm.sandpit.all_grains(include_superseded=False)
    }


def test_school_mode_trusted_grain_resolves_conflict(tmp_path):
    arm = _arm(tmp_path)
    arm.inject(
        text="Work out brackets first in the order of operations.",
        quadrant="numbers",
        teacher_id="grok",
        concept_key="order of operations brackets",
    )
    arm.inject(
        text="Work out brackets last in the order of operations.",
        quadrant="numbers",
        teacher_id="chatgpt",
        concept_key="order of operations brackets",
    )
    assert arm.open_conflicts()
    # A textbook the user vouched for, offered through the same gate.
    decision = arm.gate.offer(
        "Work out brackets first in the order of operations.",
        quadrant="numbers",
        trusted=True,
        source_name="year9-maths-sample.md",
        source_sha256="abc",
        concept_key="order of operations brackets",
    )
    assert decision.accepted is True
    assert decision.grain.trusted is True
    assert arm.open_conflicts() == []
    resolved = arm.all_conflicts()[0]
    assert resolved["status"] == "resolved"
    assert "school-mode:year9-maths-sample.md" in resolved["resolved_by"]


def test_busy_queues_then_drains(tmp_path):
    status = PitStatus()
    arm = _arm(tmp_path, pit_status=status, limits=TeacherLimits(max_queue=8))
    with status.sleep():
        result = arm.inject(
            text="Canberra is the capital city of Australia.",
            quadrant="facts",
            teacher_id="grok",
        )
        assert result.status == "queued"
        assert result.http_status == 429
        assert result.queue_position == 1
        assert arm.pending_count() == 1
        assert arm.sandpit.all_grains() == []
    # Leaving sleep marks the pit idle and drains the queue.
    assert arm.pending_count() == 0
    grains = arm.sandpit.all_grains()
    assert len(grains) == 1
    assert "Canberra" in grains[0].text


def test_rebuild_flag_also_queues(tmp_path):
    status = PitStatus()
    arm = _arm(tmp_path, pit_status=status)
    with status.rebuild():
        batch = arm.inject_many(
            [
                Injection(
                    text="A noun is a naming word.",
                    quadrant="grammar_speech",
                    teacher_id="grok",
                )
            ]
        )
        assert batch.status == "queued"
        assert batch.http_status == 429
        assert "index_rebuild" in batch.message or "rebuild" in batch.message
    assert arm.pending_count() == 0
    assert any("noun" in g.text for g in arm.sandpit.all_grains())


def test_rate_limit_returns_429(tmp_path):
    limits = TeacherLimits(max_batch_size=4, rate_per_second=0.0, burst=1, max_queue=8)
    arm = _arm(tmp_path, limits=limits)
    first = arm.inject(
        text="A unique fact about Queensland weather in November.",
        quadrant="facts",
        teacher_id="grok",
    )
    assert first.status == "promoted"
    second = arm.inject(
        text="A unique fact about Brisbane summer storms.",
        quadrant="facts",
        teacher_id="grok",
    )
    assert second.status == "rate_limited"
    assert second.http_status == 429
    # Only the first grain made it in.
    assert len(arm.sandpit.all_grains()) == 1


def test_batch_limit_rejected(tmp_path):
    limits = TeacherLimits(max_batch_size=2, rate_per_second=100.0, burst=10, max_queue=8)
    arm = _arm(tmp_path, limits=limits)
    batch = arm.inject_many(
        [
            Injection(text=f"Fact number {i} about rivers.", quadrant="facts", teacher_id="grok")
            for i in range(3)
        ]
    )
    assert batch.status == "batch_too_large"
    assert batch.http_status == 400
    assert arm.sandpit.all_grains() == []


def test_queue_full_rejected(tmp_path):
    status = PitStatus()
    limits = TeacherLimits(max_batch_size=4, rate_per_second=100.0, burst=20, max_queue=2)
    arm = _arm(tmp_path, limits=limits, pit_status=status)
    with status.sleep():
        first = arm.inject_many(
            [
                Injection(text="First queued fact about mountains.", quadrant="facts", teacher_id="grok"),
                Injection(text="Second queued fact about valleys.", quadrant="facts", teacher_id="grok"),
            ]
        )
        assert first.status == "queued"
        extra = arm.inject(
            text="Third fact that will not fit in the queue.",
            quadrant="facts",
            teacher_id="grok",
        )
        assert extra.status == "queue_full"
        assert extra.http_status == 429
        assert arm.pending_count() == 2
    # The two queued grains drain; the rejected one never enters.
    texts = " ".join(g.text for g in arm.sandpit.all_grains())
    assert "mountains" in texts and "valleys" in texts
    assert "will not fit" not in texts


def test_sleep_mode_marks_pit_busy_so_injections_queue(tmp_path):
    status = PitStatus()
    arm = _arm(tmp_path, pit_status=status)
    sleeper = SleepMode(
        arm.sandpit, staging_path=tmp_path / "sleep-staging.jsonl", pit_status=status
    )
    # Run sleep in a thread, holding it mid-pass with a barrier-like delay
    # by starting sleep while we also inject. Sleep on an empty pit is
    # instant, so we hold the busy flag the same way sleep would.
    started = threading.Event()
    finished = threading.Event()

    def _run():
        status.begin_sleep()
        started.set()
        time.sleep(0.05)
        try:
            sleeper._run()
        finally:
            status.end_sleep()
        finished.set()

    thread = threading.Thread(target=_run)
    thread.start()
    assert started.wait(1.0)
    result = arm.inject(
        text="Mercury is the closest planet to the Sun.",
        quadrant="facts",
        teacher_id="grok",
    )
    assert result.status == "queued"
    thread.join(timeout=2.0)
    assert finished.is_set()
    assert arm.pending_count() == 0
    assert any("Mercury" in g.text for g in arm.sandpit.all_grains())


def test_localhost_only_bind_refuses_other_hosts():
    with pytest.raises(BindError, match="127.0.0.1"):
        check_bind_host("0.0.0.0")
    with pytest.raises(BindError):
        check_bind_host("192.168.1.10")
    with pytest.raises(BindError):
        check_bind_host("")
    assert check_bind_host("127.0.0.1") == "127.0.0.1"
    assert check_bind_host("localhost") == "127.0.0.1"


def test_http_inject_on_localhost_requires_token(tmp_path):
    arm = _arm(tmp_path, allowlist=["grok"])
    token = ensure_token(tmp_path / "teacher_token")
    # Bind on port 0 so the OS picks a free one.
    server = serve_forever(arm, token, host="127.0.0.1", port=0)
    try:
        host, port = server.server_address
        assert host == "127.0.0.1"
        url = f"http://127.0.0.1:{port}/inject"
        body = json.dumps(
            {
                "teacher_id": "grok",
                "trusted": True,
                "block": {
                    "text": "Pythagoras' theorem relates the three sides of a right-angled triangle.",
                    "tags": ["math"],
                    "quadrant": "numbers",
                    "concept_key": "pythagoras theorem",
                },
            }
        ).encode("utf-8")

        # No token: 401
        req = Request(url, data=body, method="POST", headers={"Content-Type": "application/json"})
        with pytest.raises(HTTPError) as denied:
            urlopen(req, timeout=2)
        assert denied.value.code == 401

        # With token: accepted and granted (teacher is allowlisted).
        req = Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-DRAYL-Token": token,
            },
        )
        with urlopen(req, timeout=2) as response:
            assert response.status == 200
            payload = json.loads(response.read().decode("utf-8"))
        assert payload["status"] == "promoted"
        assert payload["items"][0]["trust_provenance"]["trusted_granted"] is True
        assert payload["items"][0]["grain"]["teacher_id"] == "grok"

        # Health does not need a token and reveals no secret.
        health = Request(f"http://127.0.0.1:{port}/health", method="GET")
        with urlopen(health, timeout=2) as response:
            info = json.loads(response.read().decode("utf-8"))
        assert info["ok"] is True
        assert info["bind"] == "127.0.0.1"
        assert "token" not in json.dumps(info).lower()
    finally:
        server.shutdown()
        server.server_close()


def test_token_file_is_created_on_first_run(tmp_path):
    path = tmp_path / "teacher_token"
    assert not path.exists()
    first = ensure_token(path)
    second = ensure_token(path)
    assert first == second
    assert path.is_file()
    assert first not in ("", "changeme")
    assert len(first) >= 16


def test_contradiction_helpers_cover_the_simple_rules():
    key = derive_concept_key("The product of two negative numbers is positive.")
    assert "product" in key and "negative" in key
    reason = contradiction_reason(
        "The product of two negative numbers is negative.",
        "The product of two negative numbers is positive.",
        "product of two negative numbers",
        "product of two negative numbers",
        0.9,
    )
    assert reason in {"conflicting_claim", "low_agreement"}
    reason_n = contradiction_reason(
        "Water boils at 100 degrees",
        "Water boils at 90 degrees",
        "water boils degrees",
        "water boils degrees",
        0.9,
    )
    assert reason_n == "differing_numbers"
    # An elaboration of the same fact is not a disagreement.
    reason_e = contradiction_reason(
        "Water boils at 100 degrees Celsius.",
        "Water boils at 100 degrees Celsius at sea level.",
        "water boils degrees celsius",
        "water boils degrees celsius",
        0.95,
    )
    assert reason_e is None

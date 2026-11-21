"""Durability acceptance tests: accepted lessons survive a hard kill.

These use real processes and SIGKILL (no clean shutdown, no Ctrl+C
handler), real threads, a real second process writing the same store,
and injected write failures.
"""

from __future__ import annotations

import json
import os
import re
import signal
import sqlite3
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from drayl.gate import ComplianceGate
from drayl.pit_status import PitStatus
from drayl.quadrants import load_quadrant_map
from drayl.sandpit import InMemorySandPit
from drayl.store import DurableStore
from drayl.teacher import Injection, TeacherArm, TeacherLimits
from drayl.teacher_server import ensure_token, serve_forever

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / "scripts" / "teacher.py"
POSIX_ONLY = pytest.mark.skipif(os.name != "posix", reason="uses SIGKILL")

NEG = "product of two negative numbers"


def _paths(tmp: Path) -> dict:
    return {
        "store": tmp / "drayl.sqlite3",
        "data": tmp / "sandpit.json",
        "staging": tmp / "near-misses.jsonl",
        "conflicts": tmp / "conflicts.jsonl",
        "token": tmp / "teacher_token",
    }


def _cli_args(tmp: Path) -> list[str]:
    p = _paths(tmp)
    return [
        "--store", str(p["store"]),
        "--data", str(p["data"]),
        "--staging", str(p["staging"]),
        "--conflicts", str(p["conflicts"]),
        "--token-file", str(p["token"]),
    ]


def _arm(tmp: Path, allowlist=(), store_timeout: float = 5.0, pit_status=None) -> TeacherArm:
    p = _paths(tmp)
    sandpit = InMemorySandPit()
    return TeacherArm(
        sandpit=sandpit,
        gate=ComplianceGate(sandpit, staging_path=p["staging"]),
        quadrant_map=load_quadrant_map(),
        allowlist=list(allowlist),
        limits=TeacherLimits(rate_per_second=1000, burst=1000, max_queue=64),
        pit_status=pit_status or PitStatus(),
        conflicts_path=p["conflicts"],
        store=DurableStore(p["store"], timeout=store_timeout),
    )


def _start_server(tmp: Path) -> tuple[subprocess.Popen, str, str]:
    proc = subprocess.Popen(
        [sys.executable, "-u", str(CLI), *_cli_args(tmp), "serve", "--port", "0"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.time() + 15
    url = None
    while url is None and time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            break
        match = re.search(r"(http://127\.0\.0\.1:\d+)", line)
        if match:
            url = match.group(1)
    assert url, f"server did not start: {proc.stderr.read() if proc.poll() is not None else ''}"
    token = _paths(tmp)["token"].read_text(encoding="utf-8").strip()
    return proc, url, token


def _kill(proc: subprocess.Popen) -> int:
    os.kill(proc.pid, signal.SIGKILL)
    proc.wait(timeout=10)
    for stream in (proc.stdout, proc.stderr):
        if stream:
            stream.close()
    return proc.returncode


def _post(url: str, token: str, path: str, body: dict) -> tuple[int, dict]:
    req = Request(
        url + path,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "X-DRAYL-Token": token},
    )
    try:
        with urlopen(req, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _get(url: str, token: str, path: str) -> tuple[int, dict]:
    req = Request(url + path, method="GET", headers={"X-DRAYL-Token": token})
    try:
        with urlopen(req, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _lesson(i: int, tag: str = "l") -> str:
    # Disjoint words per lesson so the gate never sees near-duplicates.
    return f"{tag}{i}alpha {tag}{i}bravo {tag}{i}charlie {tag}{i}delta {tag}{i}echo"


def _fresh_store(tmp: Path) -> DurableStore:
    return DurableStore(_paths(tmp)["store"])


# ---------------------------------------------------------------------------
# 1. Teach -> confirmation -> abrupt kill -> restart -> retrieve
# ---------------------------------------------------------------------------


@POSIX_ONLY
def test_teach_confirm_sigkill_restart_retrieves_lesson(tmp_path):
    proc, url, token = _start_server(tmp_path)
    status, payload = _post(
        url,
        token,
        "/inject",
        {
            "teacher_id": "grok",
            "block": {
                "text": "Water boils at 100 degrees Celsius at sea level.",
                "quadrant": "facts",
            },
        },
    )
    assert status == 200
    assert payload["status"] == "promoted"
    assert payload["saved"] is True
    grain_id = payload["items"][0]["grain"]["id"]

    # Abrupt termination: no Ctrl+C handler, no shutdown save.
    assert _kill(proc) == -signal.SIGKILL
    assert not _paths(tmp_path)["data"].exists()  # the JSON snapshot never ran

    # Restart the real server process and retrieve the lesson over HTTP.
    proc2, url2, token2 = _start_server(tmp_path)
    try:
        status, got = _get(url2, token2, f"/grains/{grain_id}")
        assert status == 200
        assert got["grain"]["text"] == "Water boils at 100 degrees Celsius at sea level."
        assert got["grain"]["status"] == "active"
        assert got["grain"]["teacher_id"] == "grok"
        # Trust policy unchanged: untrusted teacher stays untrusted.
        assert got["grain"]["trusted"] is False
    finally:
        _kill(proc2)

    store = _fresh_store(tmp_path)
    assert store.integrity_check() == "ok"
    assert [a["action"] for a in store.audit(ref_id=grain_id)] == ["grain.added"]


# ---------------------------------------------------------------------------
# 2. Several simultaneous accepted lessons all survive restart
# ---------------------------------------------------------------------------


def test_simultaneous_http_lessons_all_survive_restart(tmp_path):
    arm = _arm(tmp_path)
    token = ensure_token(tmp_path / "teacher_token")
    server = serve_forever(arm, token, host="127.0.0.1", port=0)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    results: list[tuple[int, dict]] = []
    lock = threading.Lock()
    barrier = threading.Barrier(24)

    def worker(i: int) -> None:
        barrier.wait()
        out = _post(
            url,
            token,
            "/inject",
            {"teacher_id": f"teacher{i % 4}", "block": {"text": _lesson(i), "quadrant": "facts"}},
        )
        with lock:
            results.append(out)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(24)]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
    finally:
        server.shutdown()
        server.server_close()

    assert len(results) == 24
    assert all(code == 200 and body["saved"] is True for code, body in results)
    ids = {body["items"][0]["grain"]["id"] for _, body in results}
    generations = [body["store_generation"] for _, body in results]
    assert len(ids) == 24
    # Each request committed its own transaction: 24 distinct generations.
    assert sorted(generations) == list(range(1, 25))

    # "Restart": a brand new arm on the same store file.
    reloaded = _arm(tmp_path)
    assert ids <= {g.id for g in reloaded.sandpit.all_grains()}
    assert len(reloaded.sandpit.all_grains()) == 24


def test_two_processes_writing_the_same_store_lose_nothing(tmp_path):
    script = textwrap.dedent(
        f"""
        import sys
        sys.path.insert(0, {str(ROOT)!r})
        from pathlib import Path
        from tests.test_durable import _arm, _lesson
        tag = sys.argv[1]
        arm = _arm(Path({str(tmp_path)!r}))
        for i in range(15):
            r = arm.inject(text=_lesson(i, tag), quadrant="facts", teacher_id=tag)
            assert r.status == "promoted", r
        print("done", tag)
        """
    )
    procs = [
        subprocess.Popen([sys.executable, "-c", script, tag], cwd=str(ROOT),
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for tag in ("pa", "pb", "pc")
    ]
    for proc in procs:
        out, err = proc.communicate(timeout=60)
        assert proc.returncode == 0, err
    reloaded = _arm(tmp_path)
    texts = {g.text for g in reloaded.sandpit.all_grains()}
    for tag in ("pa", "pb", "pc"):
        for i in range(15):
            assert _lesson(i, tag) in texts
    assert len(texts) == 45
    assert reloaded.store.generation() == 45


def test_stale_process_cannot_overwrite_a_newer_resolution(tmp_path):
    server_arm = _arm(tmp_path)
    server_arm.inject(text=f"The {NEG} is negative.", quadrant="numbers",
                      teacher_id="grok", concept_key=NEG)
    second = server_arm.inject(text=f"The {NEG} is positive.", quadrant="numbers",
                               teacher_id="chatgpt", concept_key=NEG)
    conflict_id = second.conflict_id
    # A second process (the CLI) resolves while the server still holds
    # the old "open" copy in memory.
    cli_arm = _arm(tmp_path)
    cli_arm.resolve(conflict_id, second.grain.id, resolved_by="human")
    # The server then commits an unrelated lesson. It must not write its
    # stale "open" conflict back over the resolution.
    result = server_arm.inject(text=_lesson(1), quadrant="facts", teacher_id="grok")
    assert result.status == "promoted"
    reloaded = _arm(tmp_path)
    record = reloaded.all_conflicts()[0]
    assert record["status"] == "resolved"
    assert record["chosen_id"] == second.grain.id
    assert server_arm.all_conflicts()[0]["status"] == "resolved"


# ---------------------------------------------------------------------------
# 3. Simulated write failure: no success, no damage
# ---------------------------------------------------------------------------


def test_simulated_write_failure_returns_503_and_keeps_existing_data(tmp_path):
    arm = _arm(tmp_path)
    kept = arm.inject(text=_lesson(1), quadrant="facts", teacher_id="grok")
    assert kept.status == "promoted"
    before_staging = _paths(tmp_path)["staging"].read_text() if _paths(tmp_path)["staging"].exists() else ""

    def fail() -> None:
        raise sqlite3.OperationalError("disk I/O error (simulated)")

    arm.store.before_commit = fail
    token = ensure_token(tmp_path / "teacher_token")
    server = serve_forever(arm, token, host="127.0.0.1", port=0)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        code, body = _post(url, token, "/inject",
                           {"teacher_id": "grok", "block": {"text": _lesson(2), "quadrant": "facts"}})
        # Also a would-be conflict: must not be staged or opened.
        code2, body2 = _post(url, token, "/inject",
                             {"teacher_id": "gemini", "block": {"text": _lesson(1) + " not", "quadrant": "facts"}})
    finally:
        server.shutdown()
        server.server_close()

    for c, b in ((code, body), (code2, body2)):
        assert c == 503
        assert b["status"] == "not_saved"
        assert b["saved"] is False
        assert "NOT saved" in b["message"]
        assert b["items"] == []

    # In memory: rolled back to the committed state.
    assert {g.text for g in arm.sandpit.all_grains()} == {_lesson(1)}
    assert arm.all_conflicts() == []
    # On disk: unchanged and healthy.
    store = _fresh_store(tmp_path)
    assert [g["text"] for g in store.load_grains()] == [_lesson(1)]
    assert store.load_conflicts() == []
    assert store.integrity_check() == "ok"
    after_staging = _paths(tmp_path)["staging"].read_text() if _paths(tmp_path)["staging"].exists() else ""
    assert after_staging == before_staging

    # Once the fault clears, the same lesson saves normally.
    arm.store.before_commit = None
    again = arm.inject_many(
        [Injection(
            text=_lesson(2), quadrant="facts", teacher_id="grok")]
    )
    assert again.http_status == 200 and again.saved is True


def test_locked_store_returns_503_not_success(tmp_path):
    arm = _arm(tmp_path, store_timeout=0.2)
    arm.inject(text=_lesson(1), quadrant="facts", teacher_id="grok")
    blocker = sqlite3.connect(str(_paths(tmp_path)["store"]), isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")  # another writer holds the lock
    try:
        result = arm.inject_many(
            [Injection(
                text=_lesson(2), quadrant="facts", teacher_id="grok")]
        )
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()
    assert result.http_status == 503
    assert result.saved is False
    assert {g.text for g in arm.sandpit.all_grains()} == {_lesson(1)}
    assert [g["text"] for g in _fresh_store(tmp_path).load_grains()] == [_lesson(1)]


def test_resolve_write_failure_leaves_conflict_open(tmp_path):
    arm = _arm(tmp_path)
    arm.inject(text=f"The {NEG} is negative.", quadrant="numbers", teacher_id="grok", concept_key=NEG)
    second = arm.inject(text=f"The {NEG} is positive.", quadrant="numbers",
                        teacher_id="chatgpt", concept_key=NEG)

    def fail() -> None:
        raise sqlite3.OperationalError("disk full (simulated)")

    arm.store.before_commit = fail
    token = ensure_token(tmp_path / "teacher_token")
    server = serve_forever(arm, token, host="127.0.0.1", port=0)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        code, body = _post(url, token, "/resolve",
                           {"conflict_id": second.conflict_id, "chosen_id": second.grain.id})
    finally:
        server.shutdown()
        server.server_close()
    assert code == 503
    assert body["status"] == "not_saved" and body["saved"] is False
    assert arm.open_conflicts()[0]["id"] == second.conflict_id
    assert second.grain.id not in {g.id for g in arm.sandpit.all_grains()}
    store = _fresh_store(tmp_path)
    assert store.load_conflicts()[0]["status"] == "open"


# ---------------------------------------------------------------------------
# 4. Interrupted updates recover to a consistent state
# ---------------------------------------------------------------------------


@POSIX_ONLY
def test_kill_in_the_middle_of_a_commit_recovers_consistently(tmp_path):
    script = textwrap.dedent(
        f"""
        import sys, time
        sys.path.insert(0, {str(ROOT)!r})
        from pathlib import Path
        from tests.test_durable import _arm, _lesson, NEG
        arm = _arm(Path({str(tmp_path)!r}))
        arm.inject(text=_lesson(1), quadrant="facts", teacher_id="grok")
        arm.inject(text="The " + NEG + " is negative.", quadrant="numbers",
                   teacher_id="grok", concept_key=NEG)
        print("committed", arm.store.generation(), flush=True)
        def hang():
            print("rows-written-not-committed", flush=True)
            time.sleep(60)
        arm.store.before_commit = hang
        # This change opens a conflict, parks a grain, stages both and
        # writes audit rows: all in one transaction that never commits.
        arm.inject(text="The " + NEG + " is positive.", quadrant="numbers",
                   teacher_id="chatgpt", concept_key=NEG)
        print("SHOULD-NOT-GET-HERE", flush=True)
        """
    )
    proc = subprocess.Popen([sys.executable, "-c", script], cwd=str(ROOT),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    lines = []
    while True:
        line = proc.stdout.readline()
        assert line, proc.stderr.read()
        lines.append(line.strip())
        if line.startswith("rows-written-not-committed"):
            break
    assert lines[0] == "committed 2"
    # The write transaction is open and its rows are in the WAL file.
    assert Path(str(_paths(tmp_path)["store"]) + "-wal").exists()
    assert _kill(proc) == -signal.SIGKILL

    store = _fresh_store(tmp_path)
    assert store.integrity_check() == "ok"
    assert store.generation() == 2
    grains = {g["text"]: g for g in store.load_grains()}
    assert set(grains) == {_lesson(1), f"The {NEG} is negative."}
    assert grains[f"The {NEG} is negative."]["status"] == "active"  # not left parked
    assert store.load_conflicts() == []
    assert store.staged() == []
    assert {a["action"] for a in store.audit()} == {"grain.added"}
    assert store.check_consistency() == []

    # And the recovered store keeps working.
    arm = _arm(tmp_path)
    again = arm.inject(text=f"The {NEG} is positive.", quadrant="numbers",
                       teacher_id="chatgpt", concept_key=NEG)
    assert again.status == "conflict"
    assert _fresh_store(tmp_path).load_conflicts()[0]["status"] == "open"


# ---------------------------------------------------------------------------
# 5. Conflict resolution survives restart, consistent with the chosen grain
# ---------------------------------------------------------------------------


@POSIX_ONLY
def test_human_resolution_survives_sigkill_and_restart(tmp_path):
    proc, url, token = _start_server(tmp_path)
    code, first = _post(url, token, "/inject", {"teacher_id": "grok", "block": {
        "text": f"The {NEG} is negative.", "quadrant": "numbers", "concept_key": NEG}})
    assert code == 200
    code, second = _post(url, token, "/inject", {"teacher_id": "chatgpt", "block": {
        "text": f"The {NEG} is positive.", "quadrant": "numbers", "concept_key": NEG}})
    assert code == 409 and second["saved"] is True
    conflict_id = second["items"][0]["conflict_id"]
    chosen = second["items"][0]["grain"]["id"]
    other = first["items"][0]["grain"]["id"]
    code, resolved = _post(url, token, "/resolve",
                           {"conflict_id": conflict_id, "chosen_id": chosen, "resolved_by": "daryl"})
    assert code == 200 and resolved["saved"] is True
    assert _kill(proc) == -signal.SIGKILL

    # Restart with the CLI (a fresh process) and with a fresh arm.
    out = subprocess.run([sys.executable, str(CLI), *_cli_args(tmp_path), "conflicts"],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0
    assert json.loads(out.stdout)["count"] == 0

    arm = _arm(tmp_path)
    record = [c for c in arm.all_conflicts() if c["id"] == conflict_id][0]
    assert record["status"] == "resolved"
    assert record["chosen_id"] == chosen
    assert record["resolved_by"] == "daryl"
    active = {g.id for g in arm.sandpit.all_grains()}
    assert chosen in active
    assert other not in active
    assert arm.sandpit.get(other).status == "conflict"  # kept, not deleted
    actions = [(a["action"], a["ref_id"]) for a in arm.store.audit()]
    assert ("conflict.opened", conflict_id) in actions
    assert ("conflict.resolved", conflict_id) in actions
    resolved_row = [a for a in arm.store.audit(ref_id=conflict_id) if a["action"] == "conflict.resolved"][0]
    assert resolved_row["detail"]["chosen_id"] == chosen
    assert resolved_row["detail"]["resolved_by"] == "daryl"
    assert arm.store.check_consistency() == []


def test_trusted_teacher_tie_break_survives_restart(tmp_path):
    arm = _arm(tmp_path, allowlist=["codex"])
    arm.inject(text=f"The {NEG} is negative.", quadrant="numbers", teacher_id="grok", concept_key=NEG)
    arm.inject(text=f"The {NEG} is positive.", quadrant="numbers", teacher_id="chatgpt", concept_key=NEG)
    later = arm.inject(text=f"The {NEG} is positive.", quadrant="numbers",
                       teacher_id="codex", trusted=True, concept_key=NEG)
    assert later.status == "promoted"
    del arm
    reloaded = _arm(tmp_path, allowlist=["codex"])
    record = reloaded.all_conflicts()[0]
    assert record["status"] == "resolved"
    assert record["chosen_id"] == later.grain.id
    assert record["resolved_by"] == "trusted-teacher:codex"
    assert later.grain.id in {g.id for g in reloaded.sandpit.all_grains()}
    assert reloaded.sandpit.get(later.grain.id).trusted is True
    assert reloaded.store.check_consistency() == []


# ---------------------------------------------------------------------------
# 6. Existing records preserved
# ---------------------------------------------------------------------------


def test_legacy_json_pit_is_imported_and_left_untouched(tmp_path):
    p = _paths(tmp_path)
    legacy = InMemorySandPit()
    old = legacy.add("An old lesson saved by the P39 JSON path.", "facts", teacher_id="grok")
    legacy.save(p["data"])
    original_bytes = p["data"].read_bytes()

    out = subprocess.run([sys.executable, str(CLI), *_cli_args(tmp_path), "show", old.id],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert "Imported 1 grains" in out.stderr
    shown = json.loads(out.stdout)
    assert shown["grain"]["text"] == old.text
    assert shown["audit"][0]["action"] == "legacy-import.grain"
    assert p["data"].read_bytes() == original_bytes  # never modified

    # Second start: nothing re-imported, nothing duplicated.
    out2 = subprocess.run([sys.executable, str(CLI), *_cli_args(tmp_path), "show", old.id],
                          capture_output=True, text=True, timeout=30)
    assert "Imported" not in out2.stderr
    assert len(_fresh_store(tmp_path).load_grains()) == 1


def test_stale_json_never_overwrites_a_stored_grain(tmp_path):
    arm = _arm(tmp_path)
    r = arm.inject(text=_lesson(1), quadrant="facts", teacher_id="grok")
    stale = InMemorySandPit()
    stale.add("tampered text", "facts", grain_id=r.grain.id)
    stale.save(_paths(tmp_path)["data"])
    counts = _fresh_store(tmp_path).merge_legacy(_paths(tmp_path)["data"], None)
    assert counts["grains"] == 0
    assert _fresh_store(tmp_path).get_grain(r.grain.id)["text"] == _lesson(1)


def test_store_refuses_to_delete_records(tmp_path):
    arm = _arm(tmp_path)
    arm.inject(text=_lesson(1), quadrant="facts", teacher_id="grok")
    conn = sqlite3.connect(str(_paths(tmp_path)["store"]))
    for table in ("grains", "audit"):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(f"DELETE FROM {table}")
    conn.close()
    assert len(_fresh_store(tmp_path).load_grains()) == 1


def test_queued_injection_is_not_reported_as_saved(tmp_path):
    status = PitStatus()
    arm = _arm(tmp_path, pit_status=status)
    with status.sleep():
        queued = arm.inject_many(
            [Injection(
                text=_lesson(1), quadrant="facts", teacher_id="grok")]
        )
        assert queued.status == "queued"
        assert queued.saved is False
        assert _fresh_store(tmp_path).load_grains() == []
    # Drained on idle, and committed then.
    assert [g["text"] for g in _fresh_store(tmp_path).load_grains()] == [_lesson(1)]


@POSIX_ONLY
def test_clean_stop_still_writes_json_snapshot_atomically(tmp_path):
    proc, url, token = _start_server(tmp_path)
    code, body = _post(url, token, "/inject",
                       {"teacher_id": "grok", "block": {"text": _lesson(7), "quadrant": "facts"}})
    assert code == 200
    proc.send_signal(signal.SIGINT)  # Ctrl+C: the old shutdown save still runs
    proc.wait(timeout=15)
    proc.stdout.close()
    proc.stderr.close()
    snapshot = json.loads(_paths(tmp_path)["data"].read_text())
    assert [g["text"] for g in snapshot["grains"]] == [_lesson(7)]
    leftovers = [n for n in os.listdir(tmp_path) if n.endswith(".tmp")]
    assert leftovers == []

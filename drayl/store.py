"""Durable store for the teacher arm (SQLite, stdlib only).

Why this exists: before this module, the teacher arm kept the live sand
pit in memory and only wrote it to JSON on Ctrl+C. A lesson could get
HTTP 200 "promoted" and then be lost if the process was killed.

Design (the smallest sound one we could find):

* One SQLite file (default ``data/drayl.sqlite3``) in WAL mode with
  ``synchronous=FULL``. SQLite's journal gives atomic commits and
  automatic recovery after a crash or a kill mid-write.
* Every accepted mutation (grains, conflict records, staged near-misses
  and the audit trail for that change) is written in ONE transaction,
  and the transaction is committed before the caller gets a success.
* Writers take ``BEGIN IMMEDIATE``, which is SQLite's write lock. Two
  threads, or two processes, cannot interleave their writes. A
  ``generation`` counter lets a process notice that another process
  committed since it last loaded, and reload before it changes anything.
* Nothing is ever deleted. Triggers refuse DELETE on grains, conflicts,
  staging and audit rows. Superseded and parked grains keep their rows.

This module does not decide trust, approval or truth. It only stores
what the teacher arm already decided.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Callable, Iterable

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS grains (
    id          TEXT PRIMARY KEY,
    status      TEXT NOT NULL,
    record      TEXT NOT NULL,
    created_gen INTEGER NOT NULL,
    updated_gen INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS conflicts (
    id          TEXT PRIMARY KEY,
    status      TEXT NOT NULL,
    record      TEXT NOT NULL,
    created_gen INTEGER NOT NULL,
    updated_gen INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS staging (
    seq    INTEGER PRIMARY KEY AUTOINCREMENT,
    gen    INTEGER NOT NULL,
    record TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
    seq    INTEGER PRIMARY KEY AUTOINCREMENT,
    gen    INTEGER NOT NULL,
    ts     REAL NOT NULL,
    op     TEXT NOT NULL,
    action TEXT NOT NULL,
    ref_id TEXT,
    detail TEXT
);
CREATE TRIGGER IF NOT EXISTS grains_never_deleted BEFORE DELETE ON grains
BEGIN SELECT RAISE(ABORT, 'grains are never deleted'); END;
CREATE TRIGGER IF NOT EXISTS conflicts_never_deleted BEFORE DELETE ON conflicts
BEGIN SELECT RAISE(ABORT, 'conflict records are never deleted'); END;
CREATE TRIGGER IF NOT EXISTS staging_never_deleted BEFORE DELETE ON staging
BEGIN SELECT RAISE(ABORT, 'staging rows are never deleted'); END;
CREATE TRIGGER IF NOT EXISTS audit_never_deleted BEFORE DELETE ON audit
BEGIN SELECT RAISE(ABORT, 'audit rows are never deleted'); END;
CREATE TRIGGER IF NOT EXISTS audit_never_updated BEFORE UPDATE ON audit
BEGIN SELECT RAISE(ABORT, 'audit rows are append-only'); END;
"""


class PersistenceError(RuntimeError):
    """A change could not be committed. Nothing was claimed as saved."""


def _dumps(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class DurableStore:
    """Transactional store for grains, conflicts, staging and audit."""

    def __init__(self, path: str | Path, timeout: float = 5.0):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.timeout = float(timeout)
        # isolation_level=None: we issue BEGIN/COMMIT ourselves.
        self._conn = sqlite3.connect(
            str(self.path),
            timeout=self.timeout,
            isolation_level=None,
            check_same_thread=False,
        )
        self._conn.execute(f"PRAGMA busy_timeout = {int(self.timeout * 1000)}")
        self.journal_mode = self._enable_wal()
        self._conn.execute("PRAGMA synchronous = FULL")
        self._in_txn = False
        # Test hook: called after the rows are written, before COMMIT.
        self.before_commit: Callable[[], None] | None = None
        # Schema statements are all IF NOT EXISTS, so two processes can
        # open the same file at once safely.
        self._conn.executescript("BEGIN IMMEDIATE;" + _SCHEMA + "COMMIT;")
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            self._conn.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES ('generation', '0')"
            )
            self._conn.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def _enable_wal(self) -> str:
        """Switch the file to WAL once. WAL is sticky, so later opens skip it.

        The switch needs a brief exclusive lock and SQLite does not apply
        the busy timeout to it, so two processes opening a brand-new file
        at once can see "database is locked". Retry until the timeout.
        """
        deadline = time.monotonic() + self.timeout
        delay = 0.01
        while True:
            try:
                mode = str(self._conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
                if mode != "wal":
                    mode = str(
                        self._conn.execute("PRAGMA journal_mode = WAL").fetchone()[0]
                    ).lower()
                return mode
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or time.monotonic() >= deadline:
                    raise
                time.sleep(delay)
                delay = min(delay * 2, 0.2)

    # ---- reads ----

    def generation(self) -> int:
        row = self._conn.execute("SELECT value FROM meta WHERE key='generation'").fetchone()
        return int(row[0]) if row else 0

    def load_grains(self) -> list[dict]:
        rows = self._conn.execute("SELECT record FROM grains ORDER BY created_gen, id").fetchall()
        return [json.loads(row[0]) for row in rows]

    def load_conflicts(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT record FROM conflicts ORDER BY created_gen, rowid"
        ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def get_grain(self, grain_id: str) -> dict | None:
        row = self._conn.execute("SELECT record FROM grains WHERE id=?", (grain_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def staged(self) -> list[dict]:
        rows = self._conn.execute("SELECT record FROM staging ORDER BY seq").fetchall()
        return [json.loads(row[0]) for row in rows]

    def audit(self, ref_id: str | None = None) -> list[dict]:
        sql = "SELECT seq, gen, ts, op, action, ref_id, detail FROM audit"
        args: tuple = ()
        if ref_id is not None:
            sql += " WHERE ref_id=?"
            args = (ref_id,)
        rows = self._conn.execute(sql + " ORDER BY seq", args).fetchall()
        return [
            {
                "seq": r[0],
                "gen": r[1],
                "ts": r[2],
                "op": r[3],
                "action": r[4],
                "ref_id": r[5],
                "detail": json.loads(r[6]) if r[6] else None,
            }
            for r in rows
        ]

    def integrity_check(self) -> str:
        return str(self._conn.execute("PRAGMA integrity_check").fetchone()[0])

    def check_consistency(self) -> list[str]:
        """Cross-table checks. Returns a list of problems (empty is good)."""
        problems: list[str] = []
        grain_ids = {row[0] for row in self._conn.execute("SELECT id FROM grains")}
        audited = {
            (row[0], row[1])
            for row in self._conn.execute("SELECT action, ref_id FROM audit")
        }
        for record in self.load_conflicts():
            cid = record.get("id")
            if record.get("status") == "resolved":
                chosen = record.get("chosen_id")
                if chosen not in grain_ids:
                    problems.append(f"conflict {cid}: chosen grain {chosen} not stored")
                if ("conflict.resolved", cid) not in audited and (
                    "legacy-import.conflict",
                    cid,
                ) not in audited:
                    problems.append(f"conflict {cid}: resolved with no audit record")
        for gid in grain_ids:
            if not any(ref == gid for _, ref in audited):
                problems.append(f"grain {gid}: no audit record")
        return problems

    # ---- writes ----

    def begin(self) -> int:
        """Take the write lock and return the committed generation."""
        if self._in_txn:
            raise PersistenceError("store transaction already open")
        try:
            self._conn.execute("BEGIN IMMEDIATE")
        except sqlite3.Error as exc:
            raise PersistenceError(f"could not lock the store: {exc}") from exc
        self._in_txn = True
        return self.generation()

    def write_changes(
        self,
        op: str,
        grains: Iterable[tuple[dict, dict | None]],
        conflicts: Iterable[tuple[dict, dict | None]],
        staged: Iterable[dict],
        extra_audit: Iterable[tuple[str, str | None, dict | None]] = (),
    ) -> int:
        """Write one change set inside the open transaction, then COMMIT.

        ``grains`` and ``conflicts`` are (new_record, previous_record or None).
        Returns the new generation. On any error the transaction is rolled
        back and PersistenceError is raised.
        """
        if not self._in_txn:
            raise PersistenceError("no store transaction open")
        conn = self._conn
        try:
            gen = self.generation() + 1
            now = time.time()

            def audit(action: str, ref_id: str | None, detail: dict | None) -> None:
                conn.execute(
                    "INSERT INTO audit(gen, ts, op, action, ref_id, detail) VALUES (?,?,?,?,?,?)",
                    (gen, now, op, action, ref_id, _dumps(detail) if detail is not None else None),
                )

            for record, previous in grains:
                status = str(record.get("status") or "active")
                if previous is None:
                    conn.execute(
                        "INSERT INTO grains(id, status, record, created_gen, updated_gen)"
                        " VALUES (?,?,?,?,?)",
                        (record["id"], status, _dumps(record), gen, gen),
                    )
                    audit(
                        "grain.added",
                        record["id"],
                        {
                            "status": status,
                            "teacher_id": record.get("teacher_id"),
                            "trusted": record.get("trusted"),
                            "quadrant": record.get("quadrant"),
                        },
                    )
                else:
                    conn.execute(
                        "UPDATE grains SET status=?, record=?, updated_gen=? WHERE id=?",
                        (status, _dumps(record), gen, record["id"]),
                    )
                    old_status = previous.get("status")
                    if old_status != status or previous.get("superseded_by") != record.get(
                        "superseded_by"
                    ):
                        audit(
                            "grain.status",
                            record["id"],
                            {
                                "from": old_status,
                                "to": status,
                                "superseded_by": record.get("superseded_by"),
                            },
                        )
                    else:
                        audit("grain.updated", record["id"], None)

            for record, previous in conflicts:
                status = str(record.get("status") or "open")
                if previous is None:
                    conn.execute(
                        "INSERT INTO conflicts(id, status, record, created_gen, updated_gen)"
                        " VALUES (?,?,?,?,?)",
                        (record["id"], status, _dumps(record), gen, gen),
                    )
                    audit(
                        "conflict.opened",
                        record["id"],
                        {
                            "grain_ids": [g.get("id") for g in record.get("grains", [])],
                            "reason": record.get("reason"),
                        },
                    )
                else:
                    conn.execute(
                        "UPDATE conflicts SET status=?, record=?, updated_gen=? WHERE id=?",
                        (status, _dumps(record), gen, record["id"]),
                    )
                if status == "resolved" and (previous is None or previous.get("status") != "resolved"):
                    audit(
                        "conflict.resolved",
                        record["id"],
                        {
                            "chosen_id": record.get("chosen_id"),
                            "resolved_by": record.get("resolved_by"),
                            "grain_ids": [g.get("id") for g in record.get("grains", [])],
                        },
                    )
                elif previous is not None and status != "resolved":
                    audit(
                        "conflict.updated",
                        record["id"],
                        {"grain_ids": [g.get("id") for g in record.get("grains", [])]},
                    )

            for record in staged:
                conn.execute(
                    "INSERT INTO staging(gen, record) VALUES (?,?)", (gen, _dumps(record))
                )
            for action, ref_id, detail in extra_audit:
                audit(action, ref_id, detail)

            conn.execute("UPDATE meta SET value=? WHERE key='generation'", (str(gen),))
            if self.before_commit is not None:
                self.before_commit()
            conn.execute("COMMIT")
        except BaseException as exc:
            self.rollback()
            if isinstance(exc, PersistenceError):
                raise
            if isinstance(exc, Exception):
                raise PersistenceError(f"save failed, nothing was committed: {exc}") from exc
            raise
        self._in_txn = False
        return gen

    def rollback(self) -> None:
        if self._in_txn:
            try:
                self._conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
        self._in_txn = False

    def merge_legacy(self, json_path: str | Path | None, conflicts_path: str | Path | None) -> dict:
        """Import a pre-durable sandpit.json / conflicts.jsonl.

        Insert-if-absent only: a stored row is never overwritten, and the
        legacy files are left exactly as they are.
        """
        counts = {"grains": 0, "conflicts": 0, "warnings": []}
        grains: list[dict] = []
        conflicts: list[dict] = []
        if json_path is not None and Path(json_path).is_file():
            try:
                payload = json.loads(Path(json_path).read_text(encoding="utf-8"))
                grains.extend(payload.get("grains", []))
                for record in payload.get("superseded", []):
                    record = dict(record)
                    # Old JSON format forced "superseded" on load; keep the
                    # status the record carries if it has one.
                    record.setdefault("status", "superseded")
                    grains.append(record)
            except (OSError, ValueError) as exc:
                counts["warnings"].append(f"could not read {json_path}: {exc}; left untouched")
        if conflicts_path is not None and Path(conflicts_path).is_file():
            try:
                for line in Path(conflicts_path).read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        conflicts.append(json.loads(line))
            except (OSError, ValueError) as exc:
                counts["warnings"].append(
                    f"could not read {conflicts_path}: {exc}; left untouched"
                )
        if not grains and not conflicts:
            return counts
        self.begin()
        try:
            known_g = {row[0] for row in self._conn.execute("SELECT id FROM grains")}
            known_c = {row[0] for row in self._conn.execute("SELECT id FROM conflicts")}
            new_g = [g for g in grains if g.get("id") and g["id"] not in known_g]
            new_c = [c for c in conflicts if c.get("id") and c["id"] not in known_c]
            if not new_g and not new_c:
                self.rollback()
                return counts
            gen = self.generation() + 1
            now = time.time()
            for record in new_g:
                self._conn.execute(
                    "INSERT INTO grains(id, status, record, created_gen, updated_gen)"
                    " VALUES (?,?,?,?,?)",
                    (record["id"], str(record.get("status") or "active"), _dumps(record), gen, gen),
                )
                self._conn.execute(
                    "INSERT INTO audit(gen, ts, op, action, ref_id, detail) VALUES (?,?,?,?,?,?)",
                    (gen, now, "legacy-import", "legacy-import.grain", record["id"],
                     _dumps({"from": str(json_path)})),
                )
            for record in new_c:
                self._conn.execute(
                    "INSERT INTO conflicts(id, status, record, created_gen, updated_gen)"
                    " VALUES (?,?,?,?,?)",
                    (record["id"], str(record.get("status") or "open"), _dumps(record), gen, gen),
                )
                self._conn.execute(
                    "INSERT INTO audit(gen, ts, op, action, ref_id, detail) VALUES (?,?,?,?,?,?)",
                    (gen, now, "legacy-import", "legacy-import.conflict", record["id"],
                     _dumps({"from": str(conflicts_path)})),
                )
            self._conn.execute("UPDATE meta SET value=? WHERE key='generation'", (str(gen),))
            self._conn.execute("COMMIT")
            self._in_txn = False
        except sqlite3.Error as exc:
            self.rollback()
            raise PersistenceError(f"legacy import failed, nothing imported: {exc}") from exc
        counts["grains"] = len(new_g)
        counts["conflicts"] = len(new_c)
        return counts

    def close(self) -> None:
        self.rollback()
        self._conn.close()


def atomic_write_json(path: str | Path, payload) -> Path:
    """Write JSON via a unique temp file, fsync, then rename over the target.

    A unique temp name (not a shared ``*.tmp``) means two writers can never
    clobber each other's half-written file. A crash leaves either the old
    file or the new one, never a torn file.
    """
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=str(destination.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, destination)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    try:
        dir_fd = os.open(str(destination.parent), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass
    return destination

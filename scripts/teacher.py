#!/usr/bin/env python3
"""Teacher arm CLI: serve on localhost, list conflicts, or resolve one.

    python scripts/teacher.py serve
    python scripts/teacher.py conflicts
    python scripts/teacher.py resolve CONFLICT_ID --choose GRAIN_ID
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from drayl.gate import ComplianceGate  # noqa: E402
from drayl.memory import InMemoryMemory  # noqa: E402
from drayl.pit_status import PitStatus  # noqa: E402
from drayl.quadrants import load_quadrant_map  # noqa: E402
from drayl.sandpit import InMemorySandPit  # noqa: E402
from drayl.sleep import SleepMode  # noqa: E402
from drayl.teacher import TeacherArm, TeacherLimits  # noqa: E402
from drayl.teacher_server import BindError, check_bind_host, ensure_token, serve_forever  # noqa: E402

DEFAULT_DATA = ROOT / "data" / "sandpit.json"
DEFAULT_STAGING = ROOT / "data" / "near-misses.jsonl"
DEFAULT_CONFLICTS = ROOT / "data" / "conflicts.jsonl"
DEFAULT_TOKEN = ROOT / "data" / "teacher_token"
DEFAULT_ALLOWLIST = ROOT / "config" / "trusted_teachers.json"
DEFAULT_LIMITS = ROOT / "config" / "teacher_limits.json"


def _load_arm(args) -> TeacherArm:
    data = Path(args.data)
    if data.is_file():
        sandpit = InMemorySandPit.load(data)
    else:
        sandpit = InMemorySandPit()
    gate = ComplianceGate(sandpit, staging_path=args.staging)
    pit_status = PitStatus()
    arm = TeacherArm(
        sandpit=sandpit,
        gate=gate,
        quadrant_map=load_quadrant_map(args.config),
        allowlist_path=args.allowlist,
        limits=TeacherLimits.from_file(args.limits),
        pit_status=pit_status,
        conflicts_path=args.conflicts,
    )
    arm._data_path = data  # type: ignore[attr-defined]
    return arm


def _save(arm: TeacherArm) -> None:
    path = getattr(arm, "_data_path", None)
    if path is not None:
        arm.sandpit.save(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="D-RAYL P39 teacher arm: localhost inject socket and conflict tools."
    )
    parser.add_argument("--data", default=str(DEFAULT_DATA), help="Sand pit JSON file.")
    parser.add_argument("--staging", default=str(DEFAULT_STAGING))
    parser.add_argument("--conflicts", default=str(DEFAULT_CONFLICTS))
    parser.add_argument("--config", default=None, help="Quadrant map JSON.")
    parser.add_argument("--allowlist", default=str(DEFAULT_ALLOWLIST))
    parser.add_argument("--limits", default=str(DEFAULT_LIMITS))
    parser.add_argument("--token-file", default=str(DEFAULT_TOKEN))
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="Listen on 127.0.0.1 for teacher injections.")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)

    sub.add_parser("conflicts", help="List open teacher disagreements.")

    resolve = sub.add_parser("resolve", help="Human resolution: choose one grain.")
    resolve.add_argument("conflict_id")
    resolve.add_argument("--choose", required=True, help="Grain id to keep.")
    resolve.add_argument("--by", default="human", help="Who is breaking the tie.")

    sleep = sub.add_parser("sleep", help="Run sleep mode, marking the pit busy first.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    arm = _load_arm(args)

    if args.command == "serve":
        try:
            check_bind_host(args.host)
        except BindError as exc:
            print(exc, file=sys.stderr)
            return 2
        token = ensure_token(args.token_file)
        server = serve_forever(arm, token, host=args.host, port=args.port)
        print(f"Teacher arm listening on {server.url}")
        print(f"Token file: {args.token_file}")
        print("Send injections to POST /inject with header X-DRAYL-Token.")
        print("This service never calls out. Teachers must be bridged locally.")
        try:
            thread = getattr(server, "_thread", None)
            if thread is not None:
                thread.join()
        except KeyboardInterrupt:
            server.shutdown()
            _save(arm)
            print("\nStopped.")
        return 0

    if args.command == "conflicts":
        open_ones = arm.open_conflicts()
        print(json.dumps({"open": open_ones, "count": len(open_ones)}, indent=2))
        return 0

    if args.command == "resolve":
        try:
            result = arm.resolve(args.conflict_id, args.choose, resolved_by=args.by)
        except (KeyError, ValueError) as exc:
            print(f"Could not resolve: {exc}", file=sys.stderr)
            return 1
        _save(arm)
        print(json.dumps(result, indent=2, default=str))
        return 0

    if args.command == "sleep":
        sleeper = SleepMode(
            arm.sandpit, staging_path=args.staging, pit_status=arm.pit_status
        )
        report = sleeper.run()
        _save(arm)
        print(json.dumps(report.as_dict(), indent=2))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())

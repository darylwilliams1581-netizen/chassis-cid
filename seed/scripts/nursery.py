#!/usr/bin/env python3
"""Nursery CLI: seed documents, school-mode load, flood, and sleep.

Run from the drayl-p38 folder:

    python scripts/nursery.py school samples/year9-maths-sample.md
    python scripts/nursery.py flood --queries 20
    python scripts/nursery.py sleep
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from drayl.nursery import (  # noqa: E402
    DEFAULT_DATA,
    DEFAULT_FLOOD_SEED,
    DEFAULT_HEAT,
    DEFAULT_STAGING,
    flood,
    load_context,
    school_path,
    seed_paths,
    write_heat,
)
from drayl.pack import build_pack  # noqa: E402
from drayl.sleep import SleepMode  # noqa: E402


def _resolve(path: str | Path) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = (Path.cwd() / candidate).resolve()
    return candidate


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="D-RAYL P38 nursery: seed, school mode, flood, and sleep."
    )
    parser.add_argument(
        "--data",
        default=str(DEFAULT_DATA),
        help="Sand pit JSON file (runs accumulate here).",
    )
    parser.add_argument(
        "--staging",
        default=str(DEFAULT_STAGING),
        help="Staging JSONL for rejected or superseded grains.",
    )
    parser.add_argument(
        "--heat",
        default=str(DEFAULT_HEAT),
        help="Where flood writes a heat-map snapshot.",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Quadrant map JSON. Default: config/quadrants.json.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    seed = sub.add_parser("seed", help="Chunk documents into untrusted grains.")
    seed.add_argument("files", nargs="+", help="One or more .txt or .md files.")

    school = sub.add_parser(
        "school",
        help="Load a fundamental document as trusted ground truth (school mode).",
    )
    school.add_argument("file", help="A .txt or .md file the user vouches for.")

    flood_cmd = sub.add_parser("flood", help="Fire synthetic queries to grow the heat map.")
    flood_cmd.add_argument("--queries", type=int, required=True, help="How many queries to fire.")
    flood_cmd.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_FLOOD_SEED,
        help="Deterministic random seed (default 38).",
    )

    sub.add_parser("sleep", help="Run school-mode sleep: trusted grains override untrusted near-duplicates in the same quadrant.")
    sub.add_parser("show", help="Print a short summary of the saved sand pit.")

    pack = sub.add_parser(
        "pack",
        help="Build an offline school pack zip from a folder of documents.",
    )
    pack.add_argument("folder", help="Folder of .txt/.md documents (searched recursively).")
    pack.add_argument(
        "--output",
        default="school-pack.zip",
        help="Zip to write (default: school-pack.zip).",
    )
    pack.add_argument("--queries", type=int, default=20, help="Synthetic flood size (default 20).")
    pack.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_FLOOD_SEED,
        help="Deterministic random seed (default 38).",
    )
    pack.add_argument(
        "--trusted",
        action="store_true",
        help="Treat every document in the folder as a trusted source.",
    )
    pack.add_argument(
        "--trust-list",
        default=None,
        help="JSON file mapping filename (or relative path) to true/false.",
    )
    pack.add_argument(
        "--trusted-file",
        action="append",
        default=[],
        dest="trusted_files",
        help="Mark one document as trusted. Repeat as needed.",
    )
    pack.add_argument(
        "--sleep",
        action="store_true",
        help="After flood, run school-mode sleep (trusted overrides untrusted).",
    )
    pack.add_argument(
        "--work-dir",
        default=None,
        help="Folder to write pack contents into before zipping.",
    )
    return parser


def _print_seed(result, label: str) -> None:
    print(f"{label}: loaded {result.path}")
    print(f"  source sha256: {result.source_sha256}")
    print(f"  trusted: {str(result.trusted).lower()}")
    print(f"  promoted: {result.promoted}")
    print(f"  rejected: {result.rejected}")
    for decision in result.decisions:
        if not decision.accepted:
            print(f"  skipped ({decision.reason})")
            continue
        grain = decision.grain
        flag = "trusted" if grain.trusted else "untrusted"
        print(f"  {grain.id} [{grain.quadrant}] ({flag}) {grain.text[:80]}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    data_path = _resolve(args.data)
    staging_path = _resolve(args.staging)
    heat_path = _resolve(args.heat)
    config_path = _resolve(args.config) if args.config else None

    sandpit, _memory, gate, quadrant_map, router = load_context(
        data_path, staging_path, config_path=config_path
    )

    if args.command == "seed":
        results = seed_paths(
            [_resolve(path) for path in args.files],
            sandpit,
            gate,
            quadrant_map,
            trusted=False,
        )
        for result in results:
            _print_seed(result, "Seed")
        sandpit.save(data_path)
        print(f"Saved {len(sandpit.all_grains())} active grains to {data_path}")
        return 0

    if args.command == "school":
        result = school_path(_resolve(args.file), sandpit, gate, quadrant_map)
        _print_seed(result, "School mode")
        sandpit.save(data_path)
        print(f"Saved {len(sandpit.all_grains())} active grains to {data_path}")
        print(
            "Note: trusted only means you vouched for this source. "
            "A wrong textbook would spread errors, so use school mode deliberately."
        )
        return 0

    if args.command == "flood":
        before = {grain.id: grain.hits for grain in sandpit.all_grains()}
        if not before:
            print("The sand pit is empty. Seed or school a document first.")
            return 1
        queries = flood(sandpit, router, args.queries, rng_seed=args.seed)
        write_heat(sandpit, heat_path)
        sandpit.save(data_path)
        total_hits = sum(grain.hits for grain in sandpit.all_grains())
        grown = sum(
            1
            for grain in sandpit.all_grains()
            if grain.hits > before.get(grain.id, 0)
        )
        print(f"Flood: ran {len(queries)} queries (seed={args.seed}).")
        print(f"  grains with more hits: {grown}")
        print(f"  total hits now: {total_hits}")
        print(f"  heat snapshot: {heat_path}")
        print(f"Saved to {data_path}")
        return 0

    if args.command == "sleep":
        report = SleepMode(sandpit, staging_path=staging_path).run()
        sandpit.save(data_path)
        print(f"Sleep: overrode {len(report.overridden)} untrusted grain(s).")
        for change in report.overridden:
            print(
                f"  {change.grain_id} in {change.quadrant} superseded_by="
                f"{change.superseded_by} (cosine={change.cosine:.3f})"
            )
        print(f"  active grains kept: {report.kept}")
        print(f"  staging (nothing was deleted): {staging_path}")
        print(f"Saved to {data_path}")
        return 0

    if args.command == "show":
        grains = sandpit.all_grains(include_superseded=True)
        active = [g for g in grains if g.status == "active"]
        superseded = [g for g in grains if g.status == "superseded"]
        trusted = [g for g in active if g.trusted]
        print(f"Sand pit: {data_path}")
        print(f"  active: {len(active)} (trusted: {len(trusted)})")
        print(f"  superseded: {len(superseded)}")
        for grain in active:
            flag = "trusted" if grain.trusted else "untrusted"
            print(
                f"  {grain.id} [{grain.quadrant}] ({flag}) hits={grain.hits} "
                f"{grain.text[:70]}"
            )
        return 0

    if args.command == "pack":
        config = _resolve(args.config) if args.config else None
        trust_list = _resolve(args.trust_list) if args.trust_list else None
        result = build_pack(
            _resolve(args.folder),
            _resolve(args.output),
            work_dir=_resolve(args.work_dir) if args.work_dir else None,
            queries=args.queries,
            folder_trusted=args.trusted,
            trust_list_path=trust_list,
            trusted_files=list(args.trusted_files or []),
            config_path=config,
            rng_seed=args.seed,
            run_sleep=args.sleep,
        )
        print(f"School pack: {result.zip_path}")
        print(f"  documents: {len(result.manifest['documents'])}")
        print(f"  grains: {result.grain_count} (trusted: {result.manifest['trusted_grain_count']})")
        print(f"  flood queries: {result.query_count}")
        print(f"  quadrants: {result.manifest['quadrant_counts']}")
        if result.manifest.get("pdfs_skipped"):
            print("  skipped PDFs (convert to .txt or .md first):")
            for name in result.manifest["pdfs_skipped"]:
                print(f"    {name}")
        print("  Unzip on target PC, point the chat box at this folder, done.")
        print(
            "  Note: trusted only means you vouched for the source. "
            "A wrong textbook would spread errors."
        )
        return 0

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())

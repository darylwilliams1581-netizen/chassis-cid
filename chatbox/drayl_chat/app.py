"""Chat box orchestrator and command-line entry point."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from drayl_chat import __label__, __version__
from drayl_chat.decoder import DecodedReply, decode
from drayl_chat.encoder import encode
from drayl_chat.gate import ComplianceGate
from drayl_chat.heatmap import HeatMap
from drayl_chat.infer import compose
from drayl_chat.memory import SqliteMemory
from drayl_chat.quadrants import load_quadrant_map
from drayl_chat.router import Router
from drayl_chat.sandpit import SandPit
from drayl_chat.sleep import SleepReport, run_sleep
from drayl_chat.writeback import WriteBack

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = PACKAGE_ROOT / "config" / "quadrants.json"
DEFAULT_SEED = PACKAGE_ROOT / "config" / "seed_grains.json"
MAX_MESSAGE = 4000


class ChatBox:
    """Wires the pipeline together and owns the data directory."""

    def __init__(
        self,
        data_dir: str | Path | None = None,
        config_path: str | Path | None = None,
        seed_path: str | Path | None = None,
        sand_delay: float = 0.0,
        memory_delay: float = 0.0,
        brain: str | Path | None = None,
    ):
        self.root = PACKAGE_ROOT
        self.brain_dir = None
        if brain:
            from drayl_chat.brain import pack_paths, resolve_brain

            pack_root = resolve_brain(brain)
            paths = pack_paths(pack_root)
            self.brain_dir = pack_root
            if data_dir is None:
                data_dir = pack_root
            if config_path is None:
                config_path = paths["config"]
            if seed_path is None:
                seed_path = paths["seed"]

        self.data_dir = Path(data_dir) if data_dir is not None else self.root / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "staging").mkdir(parents=True, exist_ok=True)

        self.quadrant_map = load_quadrant_map(config_path or DEFAULT_CONFIG)
        self.sandpit = SandPit(
            delay=sand_delay,
            state_path=self.data_dir / "sandpit_state.json",
        )
        # A school pack replaces the default starter grains.
        chosen_seed = seed_path if seed_path is not None else (
            None if self.brain_dir else DEFAULT_SEED
        )
        if chosen_seed:
            self.sandpit.load_seed(chosen_seed)
        else:
            self.sandpit._overlay_state()
        self.memory = SqliteMemory(
            self.data_dir / "memory.sqlite",
            delay=memory_delay,
        )
        self.router = Router(self.quadrant_map, self.sandpit, self.memory)
        self.heatmap = HeatMap(self.data_dir / "heatmap.jsonl")
        self.gate = ComplianceGate()
        self.writeback = WriteBack(self.data_dir / "staging", self.sandpit, self.gate)

    def close(self) -> None:
        self.router.close()
        self.sandpit.save_state()
        self.memory.close()

    def status(self) -> dict:
        staging = list((self.data_dir / "staging").glob("*.json"))
        return {
            "label": __label__,
            "version": __version__,
            "host": "127.0.0.1",
            "grains": len(self.sandpit.all_grains()),
            "memory_traces": self.memory.count(),
            "heatmap_queries": self.heatmap.query_count(),
            "staging": len(staging),
            "quadrants": list(self.quadrant_map.ids),
            "infer": "stub",
            "brain": str(self.brain_dir) if self.brain_dir else None,
            "trusted_grains": sum(
                1 for grain in self.sandpit.all_grains() if getattr(grain, "trusted", False)
            ),
        }

    def chat(self, text: str) -> dict:
        cleaned = (text or "").strip()
        if not cleaned:
            return {"ok": False, "error": "empty_message", "reply": "Please type a message first."}
        if len(cleaned) > MAX_MESSAGE:
            return {
                "ok": False,
                "error": "too_long",
                "reply": f"Please keep messages under {MAX_MESSAGE} characters.",
            }

        block = encode(cleaned, self.quadrant_map)
        merged = self.router.dispatch(block)
        draft = compose(merged)
        decoded: DecodedReply = decode(draft.text, merged, stub_notice=draft.stub_notice)

        used_ids = [hit.grain_id for hit in merged.hits]
        used_quadrants = list(merged.used_quadrants)
        self.heatmap.log(
            query=cleaned,
            grain_ids=used_ids,
            quadrants=used_quadrants,
            block_id=block.id,
        )
        for grain_id in used_ids:
            self.sandpit.mark_helped(grain_id)
        what_mattered = merged.notes[0] if merged.notes else (
            "retrieved " + ", ".join(used_ids) if used_ids else "no stored grain matched well"
        )
        self.memory.remember(
            question=cleaned,
            what_mattered=what_mattered,
            helped_grain_ids=used_ids,
            helped_quadrants=used_quadrants,
        )
        if len(used_ids) >= 2:
            self.memory.associate(used_ids[0], used_ids[1], helped=True)

        candidate_text = "Q: " + cleaned + "\nA: " + draft.text
        quadrant = used_quadrants[0] if used_quadrants else (
            self.quadrant_map.ids[0] if self.quadrant_map.ids else "misc"
        )
        staging_path = self.writeback.stage(
            text=candidate_text,
            quadrant=quadrant,
            tags=list(block.capability_tags),
            source_query=cleaned,
            reply_text=draft.text,
        )
        self.sandpit.save_state()

        return {
            "ok": True,
            "reply": decoded.text,
            "stub_notice": decoded.stub_notice,
            "block": {
                "id": block.id,
                "tokens": block.tokens,
                "capability_tags": block.capability_tags,
                "symbol_ids": block.symbol_ids,
            },
            "symbols": {
                "space": decoded.symbol_space,
                "ids": decoded.symbol_ids[:40],
                "token_count": len(decoded.tokens),
            },
            "hits": [
                {
                    "id": hit.grain_id,
                    "text": hit.text,
                    "quadrant": hit.quadrant,
                    "score": round(hit.score, 4),
                    "boosted": hit.boosted,
                    "tier": hit.tier,
                    "trusted": bool(getattr(hit, "trusted", False)),
                }
                for hit in merged.hits
            ],
            "memory": {
                "context": merged.memory.context,
                "source": merged.memory.source,
                "notes": merged.notes,
                "boosted": merged.memory_boosted,
            },
            "quadrant_order": merged.quadrant_order,
            "used_quadrants": used_quadrants,
            "staging_id": staging_path.stem,
        }

    def sleep(self) -> dict:
        report: SleepReport = run_sleep(self.sandpit, self.heatmap)
        return report.to_dict()

    def list_staging(self) -> list[dict]:
        return self.writeback.list_candidates()

    def promote(self, candidate_id: str | None = None, passing: bool = False) -> dict:
        if passing or candidate_id is None:
            results = self.writeback.promote_passing()
            return {"results": results}
        return self.writeback.promote(candidate_id)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m drayl_chat",
        description="D-RAYL Chat Box v1 (P37) — local chat plumbing, no internet."
    )
    parser.add_argument("--port", type=int, default=8790, help="Port on 127.0.0.1 (default 8790).")
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser window.")
    parser.add_argument("--sleep", action="store_true", help="Run sleep mode once and exit.")
    parser.add_argument(
        "--promote",
        action="store_true",
        help="Review staging and promote candidates that pass the gate, then exit.",
    )
    parser.add_argument("--data-dir", default=None, help="Override the data folder.")
    parser.add_argument(
        "--brain",
        default=os.environ.get("DRAYL_BRAIN"),
        help="School pack folder or zip (or set DRAYL_BRAIN). Unzip on the PC, point the chat box at this folder, done.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    box = ChatBox(data_dir=args.data_dir, brain=args.brain)
    try:
        if args.sleep:
            report = box.sleep()
            print(report["message"])
            if report["promoted"]:
                print("Promoted:", ", ".join(report["promoted"]))
            if report["demoted"]:
                print("Demoted:", ", ".join(report["demoted"]))
            return 0
        if args.promote:
            result = box.promote(passing=True)
            promoted = [item for item in result["results"] if item.get("promoted")]
            skipped = [item for item in result["results"] if not item.get("promoted")]
            print(f"Promoted {len(promoted)} candidate(s); left {len(skipped)} in staging.")
            for item in result["results"]:
                flag = "OK" if item.get("promoted") else item.get("reason")
                print(f"  {item.get('id')}: {flag}")
            return 0

        from drayl_chat.server import serve

        serve(box, port=args.port, open_browser=not args.no_browser)
        return 0
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0
    finally:
        box.close()


if __name__ == "__main__":
    sys.exit(main())

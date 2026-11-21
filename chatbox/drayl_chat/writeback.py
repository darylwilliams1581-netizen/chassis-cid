"""Write-back stub: candidate grains go to data/staging/, never auto-promoted."""

from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path

from drayl_chat.embeddings import symbols_for, tokenize
from drayl_chat.gate import ComplianceGate, GateDecision
from drayl_chat.sandpit import SandPit, make_grain

SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")


class WriteBack:
    """Stage one JSON file per candidate. Promotion is a separate, manual step."""

    def __init__(self, staging_dir: str | Path, sandpit: SandPit, gate: ComplianceGate):
        self.staging_dir = Path(staging_dir)
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        self.sandpit = sandpit
        self.gate = gate
        self._lock = threading.Lock()

    def stage(
        self,
        text: str,
        quadrant: str,
        tags: list[str] | None = None,
        source_query: str = "",
        reply_text: str = "",
        candidate_id: str | None = None,
    ) -> Path:
        cid = candidate_id or f"cand-{uuid.uuid4().hex[:12]}"
        record = {
            "id": cid,
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "source_query": source_query,
            "reply_text": reply_text,
            "text": text.strip(),
            "quadrant": quadrant or "misc",
            "tags": list(tags or []),
            "symbol_ids": symbols_for(tokenize(text)),
            "auto_promoted": False,
        }
        path = self.staging_dir / f"{cid}.json"
        with self._lock:
            path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    def list_candidates(self) -> list[dict]:
        items: list[dict] = []
        if not self.staging_dir.is_dir():
            return items
        for path in sorted(self.staging_dir.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            payload["path"] = path.name
            decision = self.gate.check(payload.get("text", ""), self.sandpit.all_grains())
            payload["gate"] = {
                "accepted": decision.accepted,
                "reason": decision.reason,
                "nearest_id": decision.nearest_id,
                "nearest_cosine": decision.nearest_cosine,
            }
            items.append(payload)
        return items

    def review(self, candidate_id: str) -> tuple[dict | None, GateDecision]:
        path = self._path_for(candidate_id)
        if path is None:
            return None, GateDecision(accepted=False, reason="not_found")
        payload = json.loads(path.read_text(encoding="utf-8"))
        decision = self.gate.check(payload.get("text", ""), self.sandpit.all_grains())
        return payload, decision

    def promote(self, candidate_id: str) -> dict:
        """Promote one candidate only if it currently passes the gate."""
        payload, decision = self.review(candidate_id)
        if payload is None:
            return {"id": candidate_id, "promoted": False, "reason": "not_found"}
        if not decision.accepted:
            return {
                "id": candidate_id,
                "promoted": False,
                "reason": decision.reason,
                "nearest_id": decision.nearest_id,
                "nearest_cosine": decision.nearest_cosine,
            }
        grain = make_grain(
            text=payload["text"],
            quadrant=payload.get("quadrant") or "misc",
            grain_id=payload["id"],
            tags=payload.get("tags") or [],
            tier="warm",
            protected=False,
        )
        self.sandpit.promote(grain)
        path = self._path_for(candidate_id)
        if path is not None:
            path.unlink()
        return {
            "id": candidate_id,
            "promoted": True,
            "reason": "promoted",
            "grain_id": grain.id,
        }

    def promote_passing(self) -> list[dict]:
        results: list[dict] = []
        for item in self.list_candidates():
            if item.get("gate", {}).get("accepted"):
                results.append(self.promote(item["id"]))
            else:
                results.append(
                    {
                        "id": item["id"],
                        "promoted": False,
                        "reason": item.get("gate", {}).get("reason", "failed"),
                        "nearest_id": item.get("gate", {}).get("nearest_id"),
                        "nearest_cosine": item.get("gate", {}).get("nearest_cosine"),
                    }
                )
        return results

    def _path_for(self, candidate_id: str) -> Path | None:
        if not SAFE_ID.match(candidate_id):
            return None
        path = self.staging_dir / f"{candidate_id}.json"
        if path.is_file():
            return path
        return None

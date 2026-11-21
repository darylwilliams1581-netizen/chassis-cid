"""Heat map: log which grains and quadrants were accessed, then aggregate."""

from __future__ import annotations

import json
import threading
from collections import Counter
from datetime import datetime
from pathlib import Path


class HeatMap:
    """Append-only JSONL log plus on-demand aggregate counts."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def log(
        self,
        query: str,
        grain_ids: list[str],
        quadrants: list[str],
        block_id: str = "",
    ) -> dict:
        record = {
            "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
            "block_id": block_id,
            "query": query,
            "grains": list(grain_ids),
            "quadrants": list(quadrants),
        }
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line)
        return record

    def entries(self) -> list[dict]:
        if not self.path.is_file():
            return []
        records: list[dict] = []
        with self._lock:
            text = self.path.read_text(encoding="utf-8")
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
        return records

    def query_count(self) -> int:
        return len(self.entries())

    def aggregates(self) -> dict:
        grain_counts: Counter[str] = Counter()
        quadrant_counts: Counter[str] = Counter()
        records = self.entries()
        for record in records:
            for gid in record.get("grains") or []:
                grain_counts[gid] += 1
            for qid in record.get("quadrants") or []:
                quadrant_counts[qid] += 1
        return {
            "queries": len(records),
            "grains": dict(grain_counts),
            "quadrants": dict(quadrant_counts),
        }

"""Static quadrant map. Loaded once at startup from a JSON config."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = PACKAGE_ROOT / "config" / "quadrants.json"


class QuadrantMap:
    """Designed (static) partition of the sand pit.

    Changing boundaries means re-tagging grains, not rebuilding any index.
    """

    def __init__(self, payload: dict):
        quadrants = payload.get("quadrants")
        if not isinstance(quadrants, list) or not quadrants:
            raise ValueError("quadrant config must have a non-empty 'quadrants' list")
        self.version = payload.get("version")
        self.description = payload.get("description", "")
        self.quadrants: list[dict] = []
        self.ids: list[str] = []
        self.by_id: dict[str, dict] = {}
        self.tag_to_quadrants: dict[str, list[str]] = {}
        for entry in quadrants:
            qid = entry["id"]
            if qid in self.by_id:
                raise ValueError(f"duplicate quadrant id: {qid}")
            tags = [str(tag).lower() for tag in entry.get("capability_tags", [])]
            record = {
                "id": qid,
                "name": entry.get("name", qid),
                "capability_tags": tags,
                "description": entry.get("description", ""),
            }
            self.quadrants.append(record)
            self.ids.append(qid)
            self.by_id[qid] = record
            for tag in tags:
                self.tag_to_quadrants.setdefault(tag, []).append(qid)

    def tags_for_tokens(self, tokens: Iterable[str]) -> list[str]:
        """Capability tags that appear in the token list, first-seen order."""
        seen: list[str] = []
        for token in tokens:
            tag = str(token).lower()
            if tag in self.tag_to_quadrants and tag not in seen:
                seen.append(tag)
        return seen

    def match_tags(self, tags: Iterable[str]) -> list[str]:
        """Return quadrant ids that match the given capability tags, in map order."""
        wanted = {str(tag).lower() for tag in tags}
        matched: list[str] = []
        for qid in self.ids:
            record_tags = set(self.by_id[qid]["capability_tags"])
            if wanted & record_tags:
                matched.append(qid)
        return matched

    def order_for(
        self, tags: Iterable[str], tokens: Iterable[str] = ()
    ) -> tuple[list[str], list[str]]:
        """Build a default search order and the list of tags that actually matched.

        Matched quadrants come first (config order among matches). The rest
        of the map follows, so every grain can still be found.
        """
        explicit = [str(tag).lower() for tag in tags]
        token_tags = [str(token).lower() for token in tokens]
        used = explicit if explicit else token_tags
        matched_ids = self.match_tags(used)
        matched_tag_list = [tag for tag in used if tag in self.tag_to_quadrants]
        seen_tags: list[str] = []
        for tag in matched_tag_list:
            if tag not in seen_tags:
                seen_tags.append(tag)
        order: list[str] = []
        seen_ids: set[str] = set()
        for qid in matched_ids + self.ids:
            if qid not in seen_ids:
                order.append(qid)
                seen_ids.add(qid)
        return order, seen_tags


def load_quadrant_map(path: str | Path | None = None) -> QuadrantMap:
    config_path = Path(path) if path is not None else DEFAULT_CONFIG
    with config_path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    return QuadrantMap(payload)

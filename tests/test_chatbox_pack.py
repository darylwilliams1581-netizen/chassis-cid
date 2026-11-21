"""The copied P37 chat box can boot from a school pack and use trusted grains."""

from __future__ import annotations

import sys
from pathlib import Path

from drayl.pack import build_pack

ROOT = Path(__file__).resolve().parent.parent
CHATBOX = ROOT / "chatbox-pack-support"
SAMPLE = ROOT / "samples" / "year9-maths-sample.md"


def _chatbox_app():
    sys.path.insert(0, str(CHATBOX))
    # Import only after the copy is on the path. Do not touch the original P37 tree.
    from drayl_chat.app import ChatBox

    return ChatBox


def test_chatbox_copy_boots_from_pack_and_answers_from_trusted_grain(tmp_path):
    source = tmp_path / "docs"
    source.mkdir()
    (source / "year9-maths-sample.md").write_text(
        SAMPLE.read_text(encoding="utf-8"), encoding="utf-8"
    )
    pack_zip = tmp_path / "school-pack.zip"
    work = tmp_path / "school-pack"
    build_pack(
        source,
        pack_zip,
        work_dir=work,
        queries=5,
        folder_trusted=True,
        rng_seed=38,
    )

    ChatBox = _chatbox_app()
    box = ChatBox(brain=work)
    try:
        status = box.status()
        assert status["grains"] >= 3
        assert status["trusted_grains"] >= 3
        assert status["brain"]
        result = box.chat(
            "What is Pythagoras in a right-angled triangle, about the hypotenuse?"
        )
        assert result["ok"] is True
        reply = result["reply"].casefold()
        assert "hypotenuse" in reply or "squared" in reply or "pythagoras" in reply
        trusted_hits = [hit for hit in result["hits"] if hit.get("trusted")]
        assert trusted_hits, f"expected a trusted hit, got {result['hits']}"
        assert "[trusted source]" in result["reply"]
    finally:
        box.close()


def test_chatbox_copy_accepts_brain_zip(tmp_path):
    source = tmp_path / "docs"
    source.mkdir()
    (source / "year9-maths-sample.md").write_text(
        SAMPLE.read_text(encoding="utf-8"), encoding="utf-8"
    )
    pack_zip = tmp_path / "school-pack.zip"
    build_pack(
        source,
        pack_zip,
        work_dir=tmp_path / "school-pack",
        queries=5,
        folder_trusted=True,
        rng_seed=38,
    )
    ChatBox = _chatbox_app()
    box = ChatBox(brain=pack_zip)
    try:
        assert box.status()["trusted_grains"] >= 1
        result = box.chat("Tell me about the hypotenuse in a right-angled triangle.")
        assert result["ok"] is True
        assert any(hit.get("trusted") for hit in result["hits"])
    finally:
        box.close()

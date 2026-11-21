"""End-to-end smoke test against the local HTTP server."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from drayl_chat.app import ChatBox
from drayl_chat.server import HOST, start_background


def test_smoke_chat_writes_heatmap_and_staging(data_dir):
    box = ChatBox(data_dir=data_dir)
    server = None
    try:
        server, port = start_background(box, port=0)
        url = f"http://{HOST}:{port}/api/chat"
        payload = json.dumps({"text": "Hello Mate, what is D-RAYL?"}).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            body = json.loads(response.read().decode("utf-8"))
            status = response.status

        assert status == 200
        assert body["ok"] is True
        assert body["reply"]
        assert "no infer engine" in body["reply"].lower() or "no infer engine" in body.get("stub_notice", "").lower()
        assert body["staging_id"]

        heatmap = data_dir / "heatmap.jsonl"
        assert heatmap.is_file(), "heatmap.jsonl was not written"
        lines = [line for line in heatmap.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(lines) >= 1
        record = json.loads(lines[0])
        assert "Hello Mate" in record["query"]
        assert "grains" in record
        assert "quadrants" in record

        staging_files = list((data_dir / "staging").glob("*.json"))
        assert staging_files, "no staging candidate was written"
        candidate = json.loads(staging_files[0].read_text(encoding="utf-8"))
        assert candidate.get("auto_promoted") is False
        assert "Hello Mate" in candidate.get("source_query", "")
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        box.close()


def test_server_status_and_sleep_endpoints(data_dir):
    box = ChatBox(data_dir=data_dir)
    server = None
    try:
        server, port = start_background(box, port=0)
        with urllib.request.urlopen(f"http://{HOST}:{port}/api/status", timeout=10) as response:
            status = json.loads(response.read().decode("utf-8"))
        assert status["label"].startswith("D-RAYL Chat Box v1")
        assert status["host"] == "127.0.0.1"
        assert status["infer"] == "stub"
        assert status["grains"] >= 2

        # Chat once so sleep has something to do, then trigger sleep.
        payload = json.dumps({"text": "What is two plus two?"}).encode("utf-8")
        request = urllib.request.Request(
            f"http://{HOST}:{port}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(request, timeout=10).read()

        sleep_req = urllib.request.Request(
            f"http://{HOST}:{port}/api/sleep",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(sleep_req, timeout=10) as response:
            report = json.loads(response.read().decode("utf-8"))
        assert report["ran"] is True
        assert report["queries"] >= 1
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        box.close()

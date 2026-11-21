"""Infer stub. There is no infer engine in v1.

A later hook could call a local model (for example Ollama) but that is
disabled, optional, and not required. This module composes a honest
reply from the merged sand grains and memory notes.
"""

from __future__ import annotations

from dataclasses import dataclass

from drayl_chat.merge import MergedResult

STUB_NOTICE = (
    "This first chat box has no infer engine yet, so I am only using "
    "what is already stored."
)

# Optional hook, off by default. Not called by the app.
OLLAMA_HOOK_ENABLED = False
OLLAMA_HOOK_URL = "http://127.0.0.1:11434/api/generate"


@dataclass
class InferDraft:
    text: str
    stub_notice: str = STUB_NOTICE
    used_grain_ids: list[str] | None = None


def compose(merged: MergedResult) -> InferDraft:
    """Build a readable reply from retrieved grains. Does not call a model."""
    lines = [f"Mate here — {STUB_NOTICE}", ""]
    used: list[str] = []

    if merged.hits:
        lines.append("I found this in the sand pit:")
        for hit in merged.hits:
            flag = " (this helped last time, according to memory)" if hit.boosted else ""
            trusted = " [trusted source]" if getattr(hit, "trusted", False) else ""
            lines.append(f"• {hit.text}{trusted}{flag}")
            used.append(hit.grain_id)
        lines.append("")
    else:
        lines.append(
            "I did not find a stored grain that matches that well enough to use."
        )
        lines.append("")

    if merged.notes:
        lines.append("From memory:")
        for note in merged.notes:
            lines.append(f"• {note}")
        lines.append("")

    if merged.used_quadrants:
        lines.append("Quadrants used: " + ", ".join(merged.used_quadrants) + ".")
        lines.append("")

    lines.append(
        "Your message and this reply are saved as a candidate in staging. "
        "Nothing is added to the sand pit automatically. "
        "You can press Sleep to tidy the stores, or Review staging to promote a candidate."
    )
    return InferDraft(text="\n".join(lines).strip(), stub_notice=STUB_NOTICE, used_grain_ids=used)


def optional_ollama_hook(_prompt: str) -> None:
    """Placeholder only. Disabled. Makes no network call."""
    if not OLLAMA_HOOK_ENABLED:
        return
    raise RuntimeError("the optional Ollama hook is disabled in v1")

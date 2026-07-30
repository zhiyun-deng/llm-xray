"""Generate human-readable markdown comparison views from the database.

One file per prompt, every model side by side. Views are derived artifacts:
regenerate them any time from results.db. Nothing here is a source of truth.
"""

from __future__ import annotations

from pathlib import Path

from . import db
from .config import VIEWS_DIR
from .prompts import Prompt


def _meta_line(row) -> str:
    bits = [f"_source: {row['source']}_"]
    if row["total_tokens"] is not None:
        bits.append(f"tokens: {row['total_tokens']}")
    # Present only on rows captured after reasoning support was added.
    if "reasoning_tokens" in row.keys() and row["reasoning_tokens"] is not None:
        bits.append(f"thinking: {row['reasoning_tokens']}")
    if row["cost_usd"] is not None:
        bits.append(f"cost: ${row['cost_usd']:.5f}")
    if row["latency_ms"] is not None:
        bits.append(f"latency: {row['latency_ms']} ms")
    if row["finish_reason"]:
        bits.append(f"finish: {row['finish_reason']}")
    if row["created_at"]:
        bits.append(f"at: {row['created_at']}")
    return " · ".join(bits)


def render_prompt(conn, prompt: Prompt) -> str:
    rows = db.responses_for_prompt(conn, prompt.id)
    out: list[str] = []
    out.append(f"# {prompt.id}")
    if prompt.title:
        out.append(f"_{prompt.title}_")
    out.append("")
    out.append("## Prompt")
    out.append("")
    # Blockquote the prompt so headings inside it don't fight the document.
    for line in prompt.text.splitlines() or [""]:
        out.append(f"> {line}")
    out.append("")

    if not rows:
        out.append("_No responses yet for this prompt._")
        out.append("")
        return "\n".join(out)

    for row in rows:
        out.append("---")
        out.append("")
        out.append(f"## {row['model']}")
        # Flag responses captured against an older version of the prompt.
        if row["prompt_hash"] and row["prompt_hash"] != prompt.hash:
            out.append("")
            out.append("> ⚠️ **STALE** — prompt text changed since this response was captured.")
        out.append("")
        out.append(_meta_line(row))
        out.append("")
        if row["error"]:
            out.append(f"**ERROR:** {row['error']}")
        else:
            out.append(row["response"].rstrip())
        out.append("")
    return "\n".join(out)


def write_view(conn, prompt: Prompt, out_dir: Path = VIEWS_DIR) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{prompt.id}.md"
    path.write_text(render_prompt(conn, prompt), encoding="utf-8")
    return path

"""Fetch the OpenRouter model catalog and write it to a browsable text file."""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

from .config import ROOT, get_api_key, load_config

CATALOG_PATH = ROOT / "models.txt"


def fetch_models() -> list[dict]:
    cfg = load_config()
    url = cfg["openrouter"]["base_url"].rstrip("/") + "/models"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {get_api_key()}"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)["data"]


def fetch_reasoning_meta(models: list[str] | None = None) -> dict[str, dict | None]:
    """Return each model's `reasoning` capability object from /models.

    Shape (any key may be absent):
      mandatory          — reasoning cannot be disabled; never send effort:none
      default_enabled    — whether it reasons when you send nothing
      default_effort     — effort to pre-select when enabling ("none" = off)
      supported_efforts  — allowed efforts, highest first. null = all accepted,
                           absent = model exposes no effort selection
      supports_max_tokens— accepts a token budget instead of/alongside effort

    A value of None means the model declares no reasoning support at all.
    """
    wanted = set(models) if models else None
    out: dict[str, dict | None] = {}
    for m in fetch_models():
        if wanted is None or m["id"] in wanted:
            meta = m.get("reasoning")
            out[m["id"]] = meta if isinstance(meta, dict) else None
    return out


def _per_million(value) -> float:
    try:
        return float(value or 0) * 1_000_000
    except (TypeError, ValueError):
        return 0.0


def write_catalog(path: Path = CATALOG_PATH) -> tuple[Path, int]:
    """Write all models, sorted by id, with input/output price and context length."""
    models = sorted(fetch_models(), key=lambda m: m["id"])

    from datetime import datetime, timezone
    header = (
        f"# OpenRouter model catalog — {len(models)} models\n"
        f"# generated {datetime.now(timezone.utc).isoformat(timespec='seconds')} "
        f"via `python3 -m llmxray catalog`\n"
        f"# columns: in$/M  out$/M  context(k)  id\n"
        f"# (prices are USD per 1,000,000 tokens)\n\n"
    )

    lines = [header]
    for m in models:
        p = m.get("pricing", {})
        pin = _per_million(p.get("prompt"))
        pout = _per_million(p.get("completion"))
        ctx = m.get("context_length") or 0
        ctx_k = f"{ctx // 1000}k" if ctx else "-"
        lines.append(f"{pin:>8.2f} {pout:>8.2f} {ctx_k:>7}  {m['id']}")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path, len(models)

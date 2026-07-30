"""Configuration and environment loading (stdlib only)."""

from __future__ import annotations

import json
import os
from pathlib import Path

# Project root is the parent of the package directory.
ROOT = Path(__file__).resolve().parent.parent

CONFIG_PATH = ROOT / "config.json"
PROMPTS_PATH = ROOT / "prompts.md"
ENV_PATH = ROOT / ".env"
DB_PATH = ROOT / "results.db"
VIEWS_DIR = ROOT / "views"

_DEFAULT_CONFIG = {
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "referer": "https://localhost/llm-xray",
        "title": "llm-xray compare",
    },
    "models": [],
    "defaults": {"temperature": 0.7, "max_tokens": 2048, "system": None, "reasoning": None},
    # Per-model reasoning overrides, keyed by model id. Wins over
    # defaults.reasoning; set a model to null to leave it on its own default.
    "reasoning_by_model": {},
    "run": {"workers": 4, "max_retries": 4, "timeout_seconds": 120},
}

# OpenRouter's effort ladder, weakest to strongest. "none" asks for no thinking.
REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")


def resolve_reasoning(cfg: dict, model: str) -> dict | None:
    """Return the reasoning object to send for `model`, or None to send nothing.

    Precedence: reasoning_by_model[model] > defaults.reasoning > None. A model
    listed in reasoning_by_model with an explicit null means "send nothing for
    this one", which is how you keep one model on its default while forcing
    others on.
    """
    by_model = cfg.get("reasoning_by_model") or {}
    if model in by_model:
        return by_model[model]
    return (cfg.get("defaults") or {}).get("reasoning")


def load_env(path: Path = ENV_PATH) -> None:
    """Load KEY=VALUE pairs from a .env file into os.environ (no overwrite)."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        # Don't clobber a value already set in the real environment.
        os.environ.setdefault(key, value)


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: Path = CONFIG_PATH) -> dict:
    """Load config.json merged over sensible defaults."""
    if not path.exists():
        return dict(_DEFAULT_CONFIG)
    data = json.loads(path.read_text(encoding="utf-8"))
    return _deep_merge(_DEFAULT_CONFIG, data)


def get_api_key() -> str:
    """Return the OpenRouter API key, raising a clear error if missing."""
    load_env()
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise SystemExit(
            "OPENROUTER_API_KEY is not set.\n"
            f"Create {ENV_PATH} (copy .env.example) or export the variable, "
            "then try again. Manual responses ('add') don't need a key."
        )
    return key

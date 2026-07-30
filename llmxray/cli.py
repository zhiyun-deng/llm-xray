"""Command-line interface for llm-xray."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile

from . import db, reasoning as reasoning_mod, views
from .catalog import CATALOG_PATH, write_catalog
from .config import REASONING_EFFORTS, VIEWS_DIR, get_api_key, load_config, resolve_reasoning
from .openrouter import OpenRouterClient
from .prompts import load_prompt_map, parse_prompts
from .runner import run as run_matrix


def _split_csv(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [x.strip() for x in value.split(",") if x.strip()]


def _select_models(cfg: dict, requested: list[str] | None) -> list[str]:
    models = cfg.get("models", [])
    if requested:
        models = requested
    if not models:
        raise SystemExit("No models configured. Add some to config.json or pass --models.")
    return models


def _reasoning_from_args(args) -> dict | None:
    """Build a reasoning override from CLI flags, or None if none were given.

    Returns the sentinel {} for --no-reasoning-override so callers can tell
    "user asked for config defaults" from "user asked for nothing".
    """
    if getattr(args, "no_reasoning", False):
        return {"effort": "none"}
    override: dict = {}
    if getattr(args, "reasoning_effort", None):
        override["effort"] = args.reasoning_effort
    if getattr(args, "reasoning_max_tokens", None):
        override["max_tokens"] = args.reasoning_max_tokens
    if getattr(args, "reasoning", False) and not override:
        override["enabled"] = True
    if getattr(args, "hide_reasoning", False):
        override["exclude"] = True
    return override or None


def _reasoning_map(cfg: dict, models: list[str], args) -> dict[str, dict | None]:
    """Resolve the reasoning object per model: CLI flags beat config."""
    override = _reasoning_from_args(args)
    if override is not None:
        return {m: override for m in models}
    return {m: resolve_reasoning(cfg, m) for m in models}


def _describe_reasoning(mapping: dict[str, dict | None]) -> None:
    shown = {m: v for m, v in mapping.items() if v is not None}
    if not shown:
        print("Reasoning: sending no reasoning parameter (each model uses its own default).")
        return
    print("Reasoning per model:")
    for m, v in mapping.items():
        print(f"  {m}: {json.dumps(v) if v is not None else 'model default (nothing sent)'}")


def _select_prompts(requested: list[str] | None):
    prompts = parse_prompts()
    if requested:
        by_id = {p.id: p for p in prompts}
        missing = [r for r in requested if r not in by_id]
        if missing:
            raise SystemExit("Unknown prompt id(s): " + ", ".join(missing))
        prompts = [by_id[r] for r in requested]
    return prompts


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #

def cmd_run(args) -> int:
    cfg = load_config()
    prompts = _select_prompts(_split_csv(args.prompts))
    if args.limit:
        prompts = prompts[: args.limit]
    models = _select_models(cfg, _split_csv(args.models))

    client = OpenRouterClient(
        api_key=get_api_key(),
        base_url=cfg["openrouter"]["base_url"],
        referer=cfg["openrouter"]["referer"],
        title=cfg["openrouter"]["title"],
        timeout=cfg["run"].get("timeout_seconds", 120),
        max_retries=cfg["run"].get("max_retries", 4),
    )
    workers = args.workers or cfg["run"].get("workers", 4)

    reasoning_for = _reasoning_map(cfg, models, args)
    _describe_reasoning(reasoning_for)

    conn = db.connect()
    summary = run_matrix(
        conn, client, prompts, models, cfg["defaults"],
        force=args.force, workers=workers, reasoning_for=reasoning_for,
    )
    return 1 if summary["errors"] else 0


def cmd_probe(args) -> int:
    """Find out empirically whether each model reasons, and if it can be toggled."""
    cfg = load_config()
    models = _select_models(cfg, _split_csv(args.models))
    arms = _split_csv(args.arms) or ["default", "on", "off"]
    unknown = [a for a in arms if a not in reasoning_mod.ARMS]
    if unknown:
        raise SystemExit(
            f"Unknown arm(s): {', '.join(unknown)}. "
            f"Choose from: {', '.join(reasoning_mod.ARMS)}"
        )

    client = OpenRouterClient(
        api_key=get_api_key(),
        base_url=cfg["openrouter"]["base_url"],
        referer=cfg["openrouter"]["referer"],
        title=cfg["openrouter"]["title"],
        timeout=cfg["run"].get("timeout_seconds", 120),
        max_retries=cfg["run"].get("max_retries", 4),
    )

    print(f"Probing {len(models)} model(s) x {len(arms)} arm(s) = "
          f"{len(models) * len(arms)} call(s). This spends real credit.\n")
    results = reasoning_mod.probe(
        client, models, arms, max_tokens=args.max_tokens, workers=args.workers or 5
    )
    print(reasoning_mod.format_table(results, arms))
    return 0


def cmd_add(args) -> int:
    prompts = load_prompt_map()
    if args.prompt not in prompts:
        raise SystemExit(f"Unknown prompt id: {args.prompt}")
    prompt = prompts[args.prompt]

    if args.file:
        text = open(args.file, encoding="utf-8").read()
    elif args.stdin or not sys.stdin.isatty():
        text = sys.stdin.read()
    else:
        text = _open_editor()
    text = text.strip()
    if not text:
        raise SystemExit("Empty response; nothing saved.")

    conn = db.connect()
    db.upsert_response(
        conn,
        prompt_id=prompt.id,
        model=args.model,
        response=text,
        source="manual",
        prompt_hash=prompt.hash,
        error=None,
    )
    print(f"Saved manual response for {prompt.id} :: {args.model} ({len(text)} chars).")
    return 0


def _open_editor() -> str:
    editor = os.environ.get("EDITOR", "nano")
    with tempfile.NamedTemporaryFile("w+", suffix=".md", delete=False) as tf:
        tf.write("")
        path = tf.name
    subprocess.call([editor, path])
    return open(path, encoding="utf-8").read()


def cmd_view(args) -> int:
    conn = db.connect()
    if args.all:
        prompts = parse_prompts()
        paths = [views.write_view(conn, p) for p in prompts]
        print(f"Wrote {len(paths)} view(s) to {VIEWS_DIR}/")
        return 0

    if not args.prompt:
        raise SystemExit("Specify a prompt id, or use --all.")
    prompts = load_prompt_map()
    if args.prompt not in prompts:
        raise SystemExit(f"Unknown prompt id: {args.prompt}")
    prompt = prompts[args.prompt]

    if args.stdout:
        sys.stdout.write(views.render_prompt(conn, prompt))
        return 0
    path = views.write_view(conn, prompt)
    print(f"Wrote {path}")
    return 0


def cmd_status(args) -> int:
    conn = db.connect()
    cfg = load_config()
    prompts = parse_prompts()
    models = _select_models(cfg, _split_csv(args.models))

    done = stale = missing = errored = 0
    rows_out: list[str] = []
    for p in prompts:
        cells = []
        for m in models:
            row = db.get_response(conn, p.id, m)
            if row is None:
                cells.append(".")
                missing += 1
            elif row["error"]:
                cells.append("E")
                errored += 1
            elif row["prompt_hash"] and row["prompt_hash"] != p.hash:
                cells.append("~")
                stale += 1
            else:
                cells.append("#")
                done += 1
        rows_out.append(f"  {''.join(cells)}  {p.id}")

    print("Legend: # ok  ~ stale  E error  . missing")
    print("Columns (models):")
    for i, m in enumerate(models):
        print(f"  col {i + 1}: {m}")
    print()
    print("\n".join(rows_out))
    print()
    total = len(prompts) * len(models)
    print(f"{done}/{total} ok · {stale} stale · {errored} error · {missing} missing")
    return 0


def cmd_stats(args) -> int:
    conn = db.connect()
    totals = db.model_totals(conn)
    if not totals:
        print("No responses recorded yet.")
        return 0
    print(f"{'model':40} {'n':>4} {'err':>4} {'tokens':>10} {'cost($)':>10} {'avg ms':>8}")
    print("-" * 80)
    grand_tokens = grand_cost = 0
    for r in totals:
        grand_tokens += r["tokens"] or 0
        grand_cost += r["cost_usd"] or 0.0
        avg = f"{r['avg_latency_ms']:.0f}" if r["avg_latency_ms"] is not None else "-"
        print(
            f"{r['model'][:40]:40} {r['n']:>4} {r['errors']:>4} "
            f"{r['tokens']:>10} {r['cost_usd']:>10.4f} {avg:>8}"
        )
    print("-" * 80)
    print(f"{'TOTAL':40} {'':>4} {'':>4} {grand_tokens:>10} {grand_cost:>10.4f}")
    return 0


def cmd_prompts(args) -> int:
    for p in parse_prompts():
        title = f" — {p.title}" if p.title else ""
        print(f"{p.id}{title}")
    return 0


def cmd_models(args) -> int:
    cfg = load_config()
    for m in cfg.get("models", []):
        print(m)
    return 0


def cmd_serve(args) -> int:
    from .web import serve
    serve(port=args.port, open_browser=not args.no_browser)
    return 0


def cmd_catalog(args) -> int:
    path, n = write_catalog()
    print(f"Wrote {n} models to {path}")
    print("Browse/filter it, e.g.:")
    print(f"  grep gpt-5 {path.name}")
    print(f"  grep gemini-3 {path.name}")
    return 0


# --------------------------------------------------------------------------- #
# parser
# --------------------------------------------------------------------------- #

def _add_reasoning_flags(p: argparse.ArgumentParser) -> None:
    """Reasoning controls shared by commands that make chat calls."""
    g = p.add_argument_group("reasoning")
    g.add_argument(
        "--reasoning", action="store_true",
        help="Turn thinking on at the provider default effort.",
    )
    g.add_argument(
        "--reasoning-effort", choices=REASONING_EFFORTS,
        help="Effort level (OpenAI/Grok-style). 'none' asks for no thinking.",
    )
    g.add_argument(
        "--reasoning-max-tokens", type=int,
        help="Thinking token budget (Anthropic/Gemini-style). Anthropic min 1024.",
    )
    g.add_argument(
        "--no-reasoning", action="store_true",
        help="Ask for no thinking (effort=none). Some models ignore this.",
    )
    g.add_argument(
        "--hide-reasoning", action="store_true",
        help="Still reason, but don't return the trace (billed the same).",
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="llmxray",
        description="Compare LLM responses across models via OpenRouter.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    pr = sub.add_parser("run", help="Fetch responses from OpenRouter for prompt x model pairs.")
    pr.add_argument("--prompts", help="Comma-separated prompt ids (default: all).")
    pr.add_argument("--models", help="Comma-separated models (default: config.json).")
    pr.add_argument("--force", action="store_true", help="Re-run pairs that already exist.")
    pr.add_argument("--workers", type=int, help="Concurrent requests.")
    pr.add_argument("--limit", type=int, help="Only the first N prompts (handy for a test run).")
    _add_reasoning_flags(pr)
    pr.set_defaults(func=cmd_run)

    pb = sub.add_parser(
        "probe",
        help="Test whether each model reasons by default, and if it can be toggled.",
    )
    pb.add_argument("--models", help="Comma-separated models (default: config.json).")
    pb.add_argument(
        "--arms",
        help="Which arms to run, comma-separated: default,on,off (default: all three).",
    )
    pb.add_argument(
        "--max-tokens", type=int, default=reasoning_mod.DEFAULT_PROBE_MAX_TOKENS,
        help=(
            f"Output cap per probe call (default {reasoning_mod.DEFAULT_PROBE_MAX_TOKENS}). "
            "Must leave room for thinking and an answer."
        ),
    )
    pb.add_argument("--workers", type=int, help="Models probed in parallel (default 5).")
    pb.set_defaults(func=cmd_probe)

    pa = sub.add_parser("add", help="Store a manually-collected response.")
    pa.add_argument("--prompt", required=True, help="Prompt id.")
    pa.add_argument("--model", required=True, help="Model name/label.")
    pa.add_argument("--file", help="Read response text from this file.")
    pa.add_argument("--stdin", action="store_true", help="Read response text from stdin.")
    pa.set_defaults(func=cmd_add)

    pv = sub.add_parser("view", help="Generate markdown comparison(s).")
    pv.add_argument("prompt", nargs="?", help="Prompt id to render.")
    pv.add_argument("--all", action="store_true", help="Render every prompt to views/.")
    pv.add_argument("--stdout", action="store_true", help="Print to stdout instead of a file.")
    pv.set_defaults(func=cmd_view)

    ps = sub.add_parser("status", help="Coverage matrix: done / stale / error / missing.")
    ps.add_argument("--models", help="Comma-separated models (default: config.json).")
    ps.set_defaults(func=cmd_status)

    pst = sub.add_parser("stats", help="Token and cost totals per model.")
    pst.set_defaults(func=cmd_stats)

    pp = sub.add_parser("prompts", help="List prompt ids.")
    pp.set_defaults(func=cmd_prompts)

    pm = sub.add_parser("models", help="List configured models.")
    pm.set_defaults(func=cmd_models)

    pc = sub.add_parser("catalog", help="Fetch the OpenRouter model list into models.txt.")
    pc.set_defaults(func=cmd_catalog)

    psv = sub.add_parser("serve", help="Launch the local web UI (run + browse).")
    psv.add_argument("--port", type=int, default=8000, help="Port (default 8000).")
    psv.add_argument("--no-browser", action="store_true", help="Don't auto-open a browser.")
    psv.set_defaults(func=cmd_serve)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)

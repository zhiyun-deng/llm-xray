"""Orchestrate running prompts x models against OpenRouter, concurrently."""

from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import db
from .openrouter import OpenRouterClient, OpenRouterError
from .prompts import Prompt


def run(
    conn,
    client: OpenRouterClient,
    prompts: list[Prompt],
    models: list[str],
    defaults: dict,
    force: bool = False,
    workers: int = 4,
    reasoning_for: dict[str, dict | None] | None = None,
) -> dict:
    """Execute the requested prompt x model matrix. Returns a summary dict."""
    # Build the worklist, skipping pairs that already have a successful response.
    jobs: list[tuple[Prompt, str]] = []
    skipped = 0
    for p in prompts:
        for m in models:
            if not force and db.has_success(conn, p.id, m):
                skipped += 1
                continue
            jobs.append((p, m))

    total = len(jobs)
    if total == 0:
        print(f"Nothing to do — {skipped} pair(s) already present. Use --force to re-run.")
        return {"ok": 0, "errors": 0, "skipped": skipped}

    print(f"Running {total} call(s) across {workers} worker(s); skipping {skipped} existing.")

    ok = 0
    errors = 0
    done = 0
    system = defaults.get("system")
    temperature = defaults.get("temperature", 0.7)
    max_tokens = defaults.get("max_tokens", 2048)

    reasoning_for = reasoning_for or {}

    def work(job: tuple[Prompt, str]):
        p, m = job
        try:
            res = client.chat(
                model=m,
                prompt=p.text,
                system=system,
                temperature=temperature,
                max_tokens=max_tokens,
                reasoning=reasoning_for.get(m),
            )
            return job, res, None
        except OpenRouterError as e:
            return job, None, str(e)
        except Exception as e:  # noqa: BLE001 - capture any unexpected failure per-job
            return job, None, f"{type(e).__name__}: {e}"

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(work, job) for job in jobs]
        for fut in as_completed(futures):
            (p, m), res, err = fut.result()
            done += 1
            if err:
                errors += 1
                db.upsert_response(
                    conn,
                    prompt_id=p.id,
                    model=m,
                    response="",
                    source="openrouter",
                    prompt_hash=p.hash,
                    error=err,
                )
                _progress(done, total, f"ERR  {p.id} :: {m} :: {err[:80]}")
            else:
                ok += 1
                db.upsert_response(
                    conn,
                    prompt_id=p.id,
                    model=m,
                    response=res.content,
                    source="openrouter",
                    prompt_hash=p.hash,
                    prompt_tokens=res.prompt_tokens,
                    completion_tokens=res.completion_tokens,
                    total_tokens=res.total_tokens,
                    cost_usd=res.cost_usd,
                    latency_ms=res.latency_ms,
                    finish_reason=res.finish_reason,
                    error=None,
                    raw_json=json.dumps(res.raw),
                    reasoning_tokens=res.reasoning_tokens,
                    reasoning=res.reasoning_text or None,
                    reasoning_config=json.dumps(reasoning_for.get(m))
                    if reasoning_for.get(m) is not None
                    else None,
                )
                think = "" if res.reasoning_tokens is None else f", {res.reasoning_tokens} think"
                _progress(done, total, f"ok   {p.id} :: {m} ({res.latency_ms} ms{think})")

    print(f"\nDone. {ok} ok, {errors} error(s), {skipped} skipped.")
    return {"ok": ok, "errors": errors, "skipped": skipped}


def _progress(done: int, total: int, msg: str) -> None:
    print(f"[{done:>{len(str(total))}}/{total}] {msg}", file=sys.stderr)

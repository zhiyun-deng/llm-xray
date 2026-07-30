"""A tiny local web UI built on the standard-library http.server.

Flow: type a prompt -> the browser fires one request per model in parallel
-> each response renders as markdown -> a Store button persists the chosen
results into results.db (and appends a new prompt to prompts.md).

No third-party dependencies. Markdown is rendered client-side via marked.js
(loaded from a CDN; falls back to plain text if offline).
"""

from __future__ import annotations

import json
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import db
from .config import REASONING_EFFORTS, get_api_key, load_config, resolve_reasoning
from .openrouter import OpenRouterClient, OpenRouterError
from .prompts import append_prompt, load_prompt_map, prompt_hash

STATIC_DIR = Path(__file__).resolve().parent / "static"

# Full API payloads keyed by (model, prompt_hash). The browser only round-trips
# display fields, so the raw JSON and reasoning trace would otherwise be lost
# between /api/run-one and /api/store.
_RAW_CACHE: dict[tuple[str, str], dict] = {}
_RAW_CACHE_MAX = 200


def _clean_reasoning(value, meta: dict | None = None) -> dict | None:
    """Validate a reasoning object arriving from the browser.

    Returns None for "send no reasoning field". Unknown keys and out-of-range
    values are dropped here so a typo surfaces as local behaviour rather than
    an opaque 400 from the provider. When the model's capability object is
    known, effort is also checked against it — notably, a mandatory-reasoning
    model never gets effort:none, which it would reject outright.
    """
    if not isinstance(value, dict):
        return None
    meta = meta or {}
    allowed = meta.get("supported_efforts")
    out: dict = {}
    effort = value.get("effort")
    if isinstance(effort, str) and effort in REASONING_EFFORTS:
        mandatory_off = effort == "none" and meta.get("mandatory") is True
        unsupported = isinstance(allowed, list) and effort not in allowed
        if not mandatory_off and not unsupported:
            out["effort"] = effort
    budget = value.get("max_tokens")
    if isinstance(budget, (int, float)) and not isinstance(budget, bool) and budget > 0:
        out["max_tokens"] = int(budget)
    # enabled:false is a real instruction ("turn thinking off") for models that
    # don't list effort:none, so it must survive even though it is falsy.
    if value.get("enabled") is False:
        if meta.get("mandatory") is not True:   # mandatory models reject disabling
            out["enabled"] = False
    elif value.get("enabled") is True and "effort" not in out and "max_tokens" not in out:
        out["enabled"] = True
    if value.get("exclude") is True:
        out["exclude"] = True
    return out or None


def _make_client(cfg: dict) -> OpenRouterClient:
    return OpenRouterClient(
        api_key=get_api_key(),
        base_url=cfg["openrouter"]["base_url"],
        referer=cfg["openrouter"]["referer"],
        title=cfg["openrouter"]["title"],
        timeout=cfg["run"].get("timeout_seconds", 120),
        max_retries=cfg["run"].get("max_retries", 4),
    )


class Handler(BaseHTTPRequestHandler):
    cfg: dict = {}
    client: OpenRouterClient | None = None
    # model -> reasoning capability object from /models (or None). Fetched once
    # at startup; empty if that lookup failed, which makes the UI fall back to
    # offering every gateway effort.
    reasoning_meta: dict = {}

    # -- helpers ---------------------------------------------------------- #
    def _send_json(self, obj, status: int = 200) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type: str) -> None:
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def log_message(self, *args) -> None:  # quieter console
        pass

    # -- routes ----------------------------------------------------------- #
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path in ("/", "/index.html"):
            self._send_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
        elif path == "/api/config":
            models = self.cfg.get("models", [])
            self._send_json({
                "models": models,
                # What config.json would send today, so the controls open
                # showing the real current state rather than a guess.
                "reasoning": {m: resolve_reasoning(self.cfg, m) for m in models},
                # Per-model capabilities, so the UI only offers what each
                # model accepts instead of a generic list.
                "reasoning_meta": {m: self.reasoning_meta.get(m) for m in models},
                "efforts": list(REASONING_EFFORTS),
            })
        elif path == "/api/stored":
            qs = parse_qs(parsed.query)
            pid = (qs.get("id") or [None])[0]
            if pid:
                self._stored_detail(pid)
            else:
                self._stored_list()
        else:
            self.send_error(404, "Not found")

    def _stored_list(self) -> None:
        conn = db.connect()
        try:
            prompts = load_prompt_map()
        except SystemExit:
            prompts = {}
        items = []
        for row in db.stored_summary(conn):
            pid = row["prompt_id"]
            p = prompts.get(pid)
            items.append({
                "prompt_id": pid,
                "title": p.title if p else "",
                "preview": (p.text[:120] if p else ""),
                "n": row["n"],
                "errors": row["errors"],
                "last_at": row["last_at"],
            })
        self._send_json({"items": items})

    def _stored_detail(self, pid: str) -> None:
        conn = db.connect()
        try:
            prompts = load_prompt_map()
        except SystemExit:
            prompts = {}
        p = prompts.get(pid)
        cur_hash = p.hash if p else None
        rows = db.responses_for_prompt(conn, pid)
        responses = []
        for r in rows:
            responses.append({
                "model": r["model"],
                "content": r["response"],
                "source": r["source"],
                "total_tokens": r["total_tokens"],
                "cost_usd": r["cost_usd"],
                "latency_ms": r["latency_ms"],
                "finish_reason": r["finish_reason"],
                "reasoning_tokens": r["reasoning_tokens"],
                "reasoning": r["reasoning"],
                "reasoning_config": r["reasoning_config"],
                "error": r["error"],
                "created_at": r["created_at"],
                "stale": bool(cur_hash and r["prompt_hash"] and r["prompt_hash"] != cur_hash),
            })
        self._send_json({
            "prompt_id": pid,
            "title": p.title if p else "",
            "prompt_text": p.text if p else "(prompt text not found in prompts.md)",
            "responses": responses,
        })

    def do_POST(self) -> None:
        try:
            if self.path == "/api/run-one":
                self._run_one()
            elif self.path == "/api/store":
                self._store()
            else:
                self.send_error(404, "Not found")
        except Exception as e:  # noqa: BLE001 - report any failure as JSON
            self._send_json({"error": f"{type(e).__name__}: {e}"}, status=500)

    def _run_one(self) -> None:
        data = self._read_json()
        prompt = (data.get("prompt") or "").strip()
        model = (data.get("model") or "").strip()
        if not prompt or not model:
            self._send_json({"error": "prompt and model are required"}, status=400)
            return
        defaults = self.cfg.get("defaults", {})
        # An explicit "reasoning" key wins, including a null meaning "send
        # nothing". Absent entirely, fall back to config.json.
        if "reasoning" in data:
            reasoning = _clean_reasoning(data.get("reasoning"), self.reasoning_meta.get(model))
        else:
            reasoning = resolve_reasoning(self.cfg, model)
        try:
            res = self.client.chat(
                model=model,
                prompt=prompt,
                system=defaults.get("system"),
                temperature=defaults.get("temperature", 0.7),
                max_tokens=defaults.get("max_tokens", 2048),
                reasoning=reasoning,
            )
        except OpenRouterError as e:
            self._send_json({"model": model, "error": str(e)})
            return

        # Stash what the browser can't carry, so /api/store can persist it.
        if len(_RAW_CACHE) > _RAW_CACHE_MAX:
            _RAW_CACHE.clear()
        _RAW_CACHE[(model, prompt_hash(prompt))] = {
            "raw": res.raw,
            "reasoning_tokens": res.reasoning_tokens,
            "reasoning": res.reasoning_text or None,
            "reasoning_config": reasoning,
        }

        self._send_json({
            "model": model,
            "content": res.content,
            "prompt_tokens": res.prompt_tokens,
            "completion_tokens": res.completion_tokens,
            "total_tokens": res.total_tokens,
            "cost_usd": res.cost_usd,
            "latency_ms": res.latency_ms,
            "finish_reason": res.finish_reason,
            "reasoning_tokens": res.reasoning_tokens,
            "reasoning": res.reasoning_text or None,
            "reasoning_config": reasoning,
            "error": None,
        })

    def _store(self) -> None:
        data = self._read_json()
        pid = (data.get("prompt_id") or "").strip()
        ptext = (data.get("prompt_text") or "").strip()
        results = data.get("results") or []
        if not pid or not ptext:
            self._send_json({"error": "prompt_id and prompt_text are required"}, status=400)
            return
        if not results:
            self._send_json({"error": "no results selected to store"}, status=400)
            return
        try:
            created = append_prompt(pid, ptext)
        except ValueError as e:
            self._send_json({"error": str(e)}, status=400)
            return

        phash = prompt_hash(ptext)
        conn = db.connect()
        stored = 0
        for r in results:
            if r.get("error"):
                continue
            extra = _RAW_CACHE.get((r["model"], phash), {})
            cfg_sent = extra.get("reasoning_config")
            db.upsert_response(
                conn,
                prompt_id=pid,
                model=r["model"],
                response=r.get("content", ""),
                source="openrouter",
                prompt_hash=phash,
                prompt_tokens=r.get("prompt_tokens"),
                completion_tokens=r.get("completion_tokens"),
                total_tokens=r.get("total_tokens"),
                cost_usd=r.get("cost_usd"),
                latency_ms=r.get("latency_ms"),
                finish_reason=r.get("finish_reason"),
                error=None,
                raw_json=json.dumps(extra["raw"]) if extra.get("raw") else None,
                reasoning_tokens=extra.get("reasoning_tokens", r.get("reasoning_tokens")),
                reasoning=extra.get("reasoning", r.get("reasoning")),
                reasoning_config=json.dumps(cfg_sent) if cfg_sent is not None else None,
            )
            stored += 1
        self._send_json({"stored": stored, "prompt_id": pid, "new_prompt": created})


def serve(port: int = 8000, open_browser: bool = True) -> None:
    cfg = load_config()
    Handler.cfg = cfg
    Handler.client = _make_client(cfg)  # validates the API key up front

    # Reasoning capabilities drive the UI controls. A failure here is not fatal:
    # the UI falls back to offering every gateway effort level.
    from .catalog import fetch_reasoning_meta
    try:
        Handler.reasoning_meta = fetch_reasoning_meta(cfg.get("models", []))
        declared = sum(1 for v in Handler.reasoning_meta.values() if v)
        print(f"Reasoning capabilities loaded for {declared} of "
              f"{len(cfg.get('models', []))} configured model(s).")
    except Exception as e:  # noqa: BLE001 - offline / bad key shouldn't block the UI
        Handler.reasoning_meta = {}
        print(f"Could not load reasoning capabilities ({type(e).__name__}); "
              "showing all effort levels.")
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}"
    print(f"llm-xray UI running at {url}  (Ctrl-C to stop)")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
        server.server_close()

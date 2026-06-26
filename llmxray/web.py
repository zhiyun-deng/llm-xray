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
from .config import get_api_key, load_config
from .openrouter import OpenRouterClient, OpenRouterError
from .prompts import append_prompt, load_prompt_map, prompt_hash

STATIC_DIR = Path(__file__).resolve().parent / "static"


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
            self._send_json({"models": self.cfg.get("models", [])})
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
        try:
            res = self.client.chat(
                model=model,
                prompt=prompt,
                system=defaults.get("system"),
                temperature=defaults.get("temperature", 0.7),
                max_tokens=defaults.get("max_tokens", 2048),
            )
        except OpenRouterError as e:
            self._send_json({"model": model, "error": str(e)})
            return
        self._send_json({
            "model": model,
            "content": res.content,
            "prompt_tokens": res.prompt_tokens,
            "completion_tokens": res.completion_tokens,
            "total_tokens": res.total_tokens,
            "cost_usd": res.cost_usd,
            "latency_ms": res.latency_ms,
            "finish_reason": res.finish_reason,
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
            )
            stored += 1
        self._send_json({"stored": stored, "prompt_id": pid, "new_prompt": created})


def serve(port: int = 8000, open_browser: bool = True) -> None:
    cfg = load_config()
    Handler.cfg = cfg
    Handler.client = _make_client(cfg)  # validates the API key up front
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

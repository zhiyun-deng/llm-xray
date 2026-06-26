# llm-xray

**See how different LLMs answer the same prompt — side by side.**

`llm-xray` runs a prompt (or a whole series of them) across many models via
[OpenRouter](https://openrouter.ai), stores every response with its cost and
token usage, and lets you compare them in a clean local web UI or as generated
markdown. Think of it as an X-ray for model behavior: one prompt in, every
model's answer laid out next to each other.

> Why not a spreadsheet? LLM answers are long, multi-paragraph markdown —
> miserable inside a cell. `llm-xray` keeps the full responses readable and the
> metadata queryable.

## Features

- 🚀 **One prompt → every model, in parallel** via OpenRouter (hundreds of models).
- 🖥️ **Local web UI** — type a prompt, watch each model answer in its own card
  with rendered markdown, then save the ones you like with one click.
- 🗂️ **Single-file storage** — every response + token/cost/latency lives in one
  SQLite file (`results.db`), not a thousand loose files.
- 🔎 **Browse past runs** — revisit any stored prompt and compare all answers.
- ✍️ **Manual responses too** — paste in answers from models not on OpenRouter.
- 🏷️ **Stale detection** — flags responses captured before you edited the prompt.
- 💵 **Cost tracking** — per-response and per-model token/cost totals.
- 📦 **Zero dependencies** — pure Python standard library. No `pip install`.

## Quick start

```bash
git clone https://github.com/<you>/llm-xray.git
cd llm-xray

# add your OpenRouter key (https://openrouter.ai/keys)
cp .env.example .env        # then edit .env

./run_this_to_start.sh      # opens the UI at http://127.0.0.1:8000
```

That's it — no virtualenv, no install step. Requires **Python 3.8+** and an
OpenRouter API key.

> On Windows, double-click `run_this_to_start.bat` (it launches via WSL).

## The web UI

```bash
./run_this_to_start.sh                 # or: python3 -m llmxray serve
```

**Compare tab** — type a prompt, hit *Run across all models* (or ⌘/Ctrl-Enter).
Each model answers in its own card with rendered markdown + token/cost/latency.
Tick the ones worth keeping, give the prompt an id, and click
*Store selected to results*.

**Browse tab** — every prompt you've stored; click one to see all model
responses side by side, with *manual* and *stale* badges where relevant.

## Configure

- **`config.json`** — which models to compare, plus temperature / max tokens / system prompt.
- **`prompts.md`** — your prompts, one human-editable file. Each starts with `## <id>`:

  ```markdown
  ## sea-haiku
  Write a haiku about the sea.

  ## explain-recursion
  Explain recursion to a curious 10-year-old in under 150 words.
  ```

  > Don't start a line *inside* a prompt body with `## ` — that begins a new prompt.

Run `python3 -m llmxray catalog` to refresh `models.txt` with the full live
OpenRouter model list (ids, prices, context length) to pick from.

## CLI

Prefer the terminal? Every UI action has a command (`python3 -m llmxray <cmd>`):

| Command | What it does |
| --- | --- |
| `serve` | Launch the local web UI (`--port`, `--no-browser`). |
| `run` | Fetch responses for every prompt × model pair. Idempotent — `--force` to re-run. |
| `add` | Store a manually-collected response (paste / file / `$EDITOR`). |
| `status` | Coverage matrix: done / stale / error / missing. |
| `view` | Generate the markdown comparison for a prompt (or `--all`). |
| `stats` | Token and cost totals per model. |
| `catalog` | Refresh `models.txt` with the live OpenRouter model list. |
| `prompts` / `models` | List configured prompts / models. |

```bash
# Run everything in config.json, then read it back
python3 -m llmxray run
python3 -m llmxray view sea-haiku --stdout
python3 -m llmxray stats

# Smoke test: first 3 prompts, two models, 8 parallel requests
python3 -m llmxray run --limit 3 --models "openai/gpt-5.5,anthropic/claude-opus-4.8" --workers 8

# Re-run one prompt across all models, overwriting old answers
python3 -m llmxray run --prompts sea-haiku --force
```

## How it fits together

```
prompts.md ─┐
config.json ─┼─► run / add / UI ──► results.db ──► view / browse
.env (key) ─┘                       (canonical)
```

`results.db` is the single source of truth. Markdown views are disposable and
regenerable any time. Your `.env` and `results.db` are git-ignored, so your API
key and collected data never get committed.

## Notes

- Markdown in the UI is rendered via [marked.js](https://marked.js.org) from a CDN;
  if offline, it falls back to plain text.
- The server binds to `127.0.0.1` only — it's a local tool, not a hosted service.

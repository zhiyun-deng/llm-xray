# llm-xray — compare LLM responses across models

A tiny, dependency-free tool to run a series of prompts against several models
(via [OpenRouter](https://openrouter.ai)) and store every response in one place
for easy side-by-side comparison.

## Why not Excel?

LLM responses are long, multi-paragraph markdown — miserable inside a spreadsheet
cell. Instead:

- **`results.db`** — a single [SQLite](https://www.sqlite.org) file is the canonical
  store of *every* response plus metadata (tokens, cost, latency, source, timestamp).
  200 prompts × 5 models = 1,000 responses still lives in **one file**, not 1,000.
- **`prompts.md`** — all your prompts in one human-editable markdown file.
- **`config.json`** — the list of models and generation settings.
- **Markdown views** — generated *on demand*, one file per prompt with all models
  side-by-side. You only create these when you want to read them.

No `pip install` needed — it's pure Python standard library (works on Python 3.8+).

## Setup

1. Get an OpenRouter API key: https://openrouter.ai/keys
2. Copy the example env file and add your key:
   ```bash
   cp .env.example .env       # then edit .env
   ```
   (Manual responses don't need a key — only `run` does.)
3. Edit `config.json` to list the models you want to compare.

## Define prompts

Edit `prompts.md`. Each prompt starts with `## <id>` (a slug, no spaces); the body
runs until the next `## ` line:

```markdown
## sea-haiku
Write a haiku about the sea.

## explain-recursion
Explain recursion to a curious 10-year-old in under 150 words.
```

> Don't start a line *inside* a prompt body with `## ` — that begins a new prompt.
> Use `###` or indent it if you need a heading within the prompt text.

## Web UI (easiest)

```bash
./run_this_to_start.sh                 # the launcher — opens http://127.0.0.1:8000
# (equivalent to: python3 -m llmxray serve)
```

Two tabs:

- **Compare** — type a prompt, hit *Run across all models* (or ⌘/Ctrl-Enter).
  Each model answers in its own card with rendered markdown + token/cost/latency.
  Tick the ones you want, enter a prompt id, and click *Store selected to results*
  (saves to `results.db` and appends the prompt to `prompts.md` if it's new).
- **Browse stored** — every prompt you've stored, click one to see all model
  responses side-by-side (manual vs. fetched, and a *stale* badge if the prompt
  text changed since capture).

Pure stdlib server, runs locally only. Markdown is rendered via marked.js (CDN).

## CLI

All commands are `python3 -m llmxray <command>`.

| Command | What it does |
| --- | --- |
| `serve` | Launch the local web UI (`--port`, `--no-browser`). |
| `run` | Fetch responses from OpenRouter for every prompt × model pair. Skips pairs that already exist (idempotent) — use `--force` to re-run. |
| `add` | Store a response you collected manually (paste / file / `$EDITOR`). |
| `status` | Coverage matrix: which pairs are done / stale / errored / missing. |
| `view` | Generate the markdown comparison for a prompt (or `--all`). |
| `stats` | Token and cost totals per model. |
| `catalog` | Fetch the live OpenRouter model list into `models.txt`. |
| `prompts` / `models` | List configured prompts / models. |

### Run the comparison

```bash
# Everything (all prompts × all models in config.json)
python3 -m llmxray run

# A quick smoke test: first 3 prompts, two models, 8 parallel requests
python3 -m llmxray run --limit 3 --models "openai/gpt-4o,anthropic/claude-3.5-sonnet" --workers 8

# Re-run a single prompt across all models, overwriting old answers
python3 -m llmxray run --prompts sea-haiku --force
```

### Add a response manually

For models not on OpenRouter, or answers gathered elsewhere:

```bash
python3 -m llmxray add --prompt sea-haiku --model "some/model" --file answer.md
cat answer.md | python3 -m llmxray add --prompt sea-haiku --model "some/model" --stdin
python3 -m llmxray add --prompt sea-haiku --model "some/model"   # opens $EDITOR
```

### Read the results

```bash
python3 -m llmxray view sea-haiku            # writes views/sea-haiku.md
python3 -m llmxray view sea-haiku --stdout   # print to terminal
python3 -m llmxray view --all                # one file per prompt in views/
```

Each view shows the prompt followed by every model's response with a metadata line,
and flags any response as **STALE** if the prompt text changed after it was captured.

### Check progress / cost

```bash
python3 -m llmxray status   # legend: # ok  ~ stale  E error  . missing
python3 -m llmxray stats    # per-model tokens, cost, avg latency
```

## How it fits together

```
prompts.md ─┐
config.json ─┼─► run / add ──► results.db ──► view ──► views/<id>.md
.env (key) ─┘                  (canonical)            (read these)
```

`results.db` is the source of truth; `views/` is disposable and regenerable any time.

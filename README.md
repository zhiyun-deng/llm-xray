# llm-xray

A tiny, dependency-free tool to run a series of prompts against several models
(via [OpenRouter](https://openrouter.ai)) and store every response in one place
for easy side-by-side comparison.

It does two things:

- **Compare** — send one prompt to many models at once and see their answers laid
  out next to each other, rendered as markdown.
- **Store** — keep every response (with cost and token usage) in a single file you
  can browse and revisit any time.

> Why not a spreadsheet? LLM answers are long, multi-paragraph markdown —
> miserable inside a cell. `llm-xray` keeps the full responses readable and the
> metadata queryable.

## Features

- **One prompt, every model, in parallel** via OpenRouter (hundreds of models).
- **Local web UI** — type a prompt, watch each model answer in its own card
  with rendered markdown, then save the ones you like with one click.
- **Single-file storage** — every response + token/cost/latency lives in one
  SQLite file (`results.db`), not a thousand loose files.
- **Browse past runs** — revisit any stored prompt and compare all answers.
- **Manual responses too** — paste in answers from models not on OpenRouter.
- **Stale detection** — flags responses captured before you edited the prompt.
- **Cost tracking** — per-response and per-model token/cost totals.
- **Zero dependencies** — pure Python standard library. No `pip install`.

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
| `probe` | Test which models reason ("think") by default, and whether it can be toggled. |
| `prompts` / `models` | List configured prompts / models. |

### Run the comparison

```bash
# Everything (all prompts × all models in config.json)
python3 -m llmxray run

# A quick smoke test: first 3 prompts, two models, 8 parallel requests
python3 -m llmxray run --limit 3 --models "openai/gpt-5.5,anthropic/claude-opus-4.8" --workers 8

# Re-run a single prompt across all models, overwriting old answers
python3 -m llmxray run --prompts sea-haiku --force
```

### Reasoning ("thinking")

Models differ on whether they think by default, which makes a raw comparison
unfair — you can end up comparing a reasoning model against a non-reasoning
one. `probe` calls each model three ways (parameter unset / forced on / forced
off) and reports what actually happened, read from
`usage.completion_tokens_details.reasoning_tokens`:

```bash
python3 -m llmxray probe                      # all configured models
python3 -m llmxray probe --arms default       # just "what does it do normally?"
```

Then control it per run, or pin it per model in `config.json`:

```bash
python3 -m llmxray run --reasoning                    # on, provider default effort
python3 -m llmxray run --reasoning-effort high        # OpenAI / Grok style
python3 -m llmxray run --reasoning-max-tokens 4096    # Anthropic / Gemini style
python3 -m llmxray run --no-reasoning                 # ask for none (some ignore it)
```

```json
"defaults":   { "reasoning": { "effort": "medium" } },
"reasoning_by_model": {
  "anthropic/claude-opus-4.8": { "max_tokens": 4096 },
  "deepseek/deepseek-v4-pro":  { "effort": "high" },
  "openai/gpt-5.5":            null
}
```

`reasoning_by_model` wins over `defaults.reasoning`; an explicit `null` means
"send nothing for this model, leave it on its own default". CLI flags override
both. Reasoning tokens are billed as output **and** count against
`max_tokens` — if a thinking model returns `finish_reason: "length"` with a
short answer, raise `defaults.max_tokens`.

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

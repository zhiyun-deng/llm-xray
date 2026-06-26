#!/usr/bin/env bash
# Double-click or run this to launch the llm-xray web UI.
# Any extra args are passed through (e.g. ./run_this_to_start.sh --port 9000).

# Work from this script's own folder, so it runs from anywhere.
cd "$(dirname "$0")" || exit 1

# Pick whatever Python is available.
PY="$(command -v python3 || command -v python)"
if [ -z "$PY" ]; then
  echo "Python 3 was not found on your PATH. Install it and try again." >&2
  exit 1
fi

if [ ! -f .env ]; then
  echo "WARNING: no .env file found — running models needs an OPENROUTER_API_KEY."
  echo "         Copy .env.example to .env and add your key, then re-run."
  echo
fi

echo "Starting llm-xray UI...  (press Ctrl-C to stop)"
exec "$PY" -m llmxray serve "$@"

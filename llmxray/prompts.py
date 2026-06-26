"""Parse the human-editable prompts.md file.

Format: each prompt begins with a line "## <id> [optional title]".
The body is everything up to the next such line. Text before the first
"## " line is ignored (used for notes/instructions).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from .config import PROMPTS_PATH

_HEADER_RE = re.compile(r"^##\s+(\S.*)$")


@dataclass
class Prompt:
    id: str
    title: str
    text: str

    @property
    def hash(self) -> str:
        return prompt_hash(self.text)


def prompt_hash(text: str) -> str:
    """Stable hash of prompt text, used to detect stale responses."""
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]


def parse_prompts(path: Path = PROMPTS_PATH) -> list[Prompt]:
    if not path.exists():
        raise SystemExit(f"Prompts file not found: {path}")

    lines = path.read_text(encoding="utf-8").splitlines()
    prompts: list[Prompt] = []
    seen: set[str] = set()

    cur_id: str | None = None
    cur_title = ""
    cur_body: list[str] = []

    def flush() -> None:
        if cur_id is None:
            return
        text = "\n".join(cur_body).strip()
        prompts.append(Prompt(id=cur_id, title=cur_title, text=text))

    for lineno, line in enumerate(lines, 1):
        m = _HEADER_RE.match(line)
        if m:
            flush()
            header = m.group(1).strip()
            parts = header.split(None, 1)
            pid = parts[0]
            title = parts[1].strip() if len(parts) > 1 else ""
            if pid in seen:
                raise SystemExit(
                    f"Duplicate prompt id '{pid}' at {path}:{lineno}"
                )
            seen.add(pid)
            cur_id, cur_title, cur_body = pid, title, []
        elif cur_id is not None:
            cur_body.append(line)
    flush()

    # Warn (don't fail) on empty bodies — caught here so runs don't waste calls.
    empties = [p.id for p in prompts if not p.text]
    if empties:
        raise SystemExit(
            "These prompts have an empty body: " + ", ".join(empties)
        )
    if not prompts:
        raise SystemExit(
            f"No prompts found in {path}. Add one starting with '## <id>'."
        )
    return prompts


def load_prompt_map(path: Path = PROMPTS_PATH) -> dict[str, Prompt]:
    return {p.id: p for p in parse_prompts(path)}


def append_prompt(pid: str, text: str, path: Path = PROMPTS_PATH) -> bool:
    """Append a new prompt to prompts.md. Returns False if the id already exists.

    Raises ValueError if the id is not a single slug-like token.
    """
    if not pid or len(pid.split()) != 1:
        raise ValueError("Prompt id must be a single token with no spaces.")
    existing = {p.id for p in parse_prompts(path)} if path.exists() else set()
    if pid in existing:
        return False
    text = text.strip()
    sep = "" if not path.exists() or path.read_text(encoding="utf-8").endswith("\n") else "\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"{sep}\n## {pid}\n{text}\n")
    return True

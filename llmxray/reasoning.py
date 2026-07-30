"""Empirically determine each model's reasoning ("thinking") behaviour.

OpenRouter's /models metadata says which models *accept* the reasoning
parameter, but not whether a model reasons when you don't ask it to, nor
whether it can be told to stop. The only reliable answer is to call the model
and read usage.completion_tokens_details.reasoning_tokens back, which is what
this module does.

Three arms per model:
  default — send no reasoning field at all; reveals the model's own default
  on      — reasoning {"enabled": true}; confirms thinking can be turned on
  off     — reasoning {"effort": "none"}; reveals whether it can be turned off
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from .openrouter import OpenRouterClient, OpenRouterError

# A question that a thinking model will actually spend tokens on. This has to
# be genuinely hard: models with adaptive thinking (Claude 4.x) reason only as
# much as a question warrants, so an easy prompt returns zero reasoning tokens
# even with reasoning explicitly enabled — a false "cannot be turned on".
PROBE_PROMPT = (
    "Three people check into a hotel room that costs $30. They each pay $10. "
    "Later the clerk realizes the room was only $25 and sends $5 back with the "
    "bellhop, who keeps $2 and returns $1 to each guest. Each guest paid $9, "
    "totaling $27, plus the bellhop's $2 is $29. Where is the missing dollar? "
    "Explain precisely what is wrong with the accounting."
)

# Reasoning tokens also come out of the output cap, so the cap must leave room
# for thinking *and* an answer or the probe reads as truncation, not thinking.
DEFAULT_PROBE_MAX_TOKENS = 4096

ARMS: dict[str, dict | None] = {
    "default": None,
    "on": {"enabled": True},
    "off": {"effort": "none"},
}


@dataclass
class ProbeCell:
    arm: str
    reasoning_tokens: int | None = None
    completion_tokens: int | None = None
    cost_usd: float | None = None
    finish_reason: str | None = None
    has_trace: bool = False
    error: str | None = None

    @property
    def thought(self) -> bool | None:
        """True/False if the provider reported a count, None if it didn't."""
        if self.error or self.reasoning_tokens is None:
            return None
        return self.reasoning_tokens > 0


def probe_model(
    client: OpenRouterClient,
    model: str,
    arms: list[str],
    max_tokens: int = DEFAULT_PROBE_MAX_TOKENS,
) -> dict[str, ProbeCell]:
    out: dict[str, ProbeCell] = {}
    for arm in arms:
        try:
            res = client.chat(
                model=model,
                prompt=PROBE_PROMPT,
                temperature=1.0,
                max_tokens=max_tokens,
                reasoning=ARMS[arm],
            )
        except OpenRouterError as e:
            out[arm] = ProbeCell(arm=arm, error=str(e)[:160])
            continue
        out[arm] = ProbeCell(
            arm=arm,
            reasoning_tokens=res.reasoning_tokens,
            completion_tokens=res.completion_tokens,
            cost_usd=res.cost_usd,
            finish_reason=res.finish_reason,
            has_trace=bool(res.reasoning_text.strip()),
        )
    return out


def probe(
    client: OpenRouterClient,
    models: list[str],
    arms: list[str],
    max_tokens: int = DEFAULT_PROBE_MAX_TOKENS,
    workers: int = 5,
) -> dict[str, dict[str, ProbeCell]]:
    """Probe every model. Arms run in series per model, models in parallel."""
    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = ex.map(lambda m: probe_model(client, m, arms, max_tokens), models)
        return dict(zip(models, results))


def verdict(cells: dict[str, ProbeCell]) -> str:
    """One-line interpretation of a model's probe row."""
    default = cells.get("default")
    on = cells.get("on")
    off = cells.get("off")

    if default is None:
        return "no default arm run"
    if default.error:
        return f"probe failed: {default.error}"
    if default.reasoning_tokens is None:
        # No usage breakdown at all: the provider hides it either way.
        if on and on.thought:
            return "OFF by default; ON when asked (provider omits count by default)"
        return "provider reports no reasoning-token count — inconclusive"

    if default.thought:
        if off and off.error and "mandatory" in off.error.lower():
            return "ALWAYS ON — provider rejects disabling"
        if off and off.thought is False:
            return "ON by default; can be turned off"
        if off and off.thought:
            return "ALWAYS ON — 'off' was accepted but it still reasoned"
        if off and off.error:
            return f"ON by default; disabling errored: {off.error}"
        return "ON by default"

    if on and on.thought:
        return "OFF by default; can be turned on"
    if on and on.error:
        return f"OFF by default; enabling errored: {on.error}"
    # Adaptive-thinking models spend zero tokens on questions they find easy,
    # so this can still mean "on but unused" rather than "cannot be enabled".
    return "OFF by default; no thinking even when enabled (try a harder prompt / higher effort)"


def format_table(results: dict[str, dict[str, ProbeCell]], arms: list[str]) -> str:
    """Render the probe as a fixed-width table plus per-model verdicts."""
    lines: list[str] = []
    width = max((len(m) for m in results), default=20)
    header = f"{'model':{width}}  " + "  ".join(f"{a:>14}" for a in arms)
    lines.append(header)
    lines.append("-" * len(header))

    total_cost = 0.0
    for model, cells in results.items():
        row = [f"{model:{width}}"]
        for arm in arms:
            c = cells.get(arm)
            if c is None:
                row.append(f"{'-':>14}")
            elif c.error:
                row.append(f"{'ERR':>14}")
            elif c.reasoning_tokens is None:
                row.append(f"{'n/a':>14}")
            else:
                mark = "think" if c.reasoning_tokens else "none"
                row.append(f"{f'{c.reasoning_tokens} {mark}':>14}")
            total_cost += (c.cost_usd or 0.0) if c else 0.0
        lines.append("  ".join(row))

    lines.append("")
    lines.append("Reasoning tokens per arm (0 = model did not think).")
    lines.append("")
    for model, cells in results.items():
        lines.append(f"  {model:{width}}  {verdict(cells)}")
    lines.append("")
    lines.append(f"Probe cost: ${total_cost:.4f}")
    return "\n".join(lines)

"""Data layer for the TUI: normalization, aggregation, formatting.

Pure functions only — no textual imports. This is the Python port of the web
dashboard's client-side logic: turn normalization mirrors
dashboard/src/useEvents.ts, KPI aggregation mirrors dashboard/src/App.tsx,
formatters mirror dashboard/src/format.ts. If the numbers here ever disagree
with the web dashboard on the same data, this module is the bug.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

MAX_TURNS = 1000


@dataclass(frozen=True)
class Turn:
    id: int
    ts: float
    session_id: str | None
    agent_id: str | None
    model: str | None
    status: int
    stop_reason: str | None
    input_tokens: int
    output_tokens: int
    cache_read: int
    cache_creation: int
    ttft_s: float | None
    total_s: float | None
    metrics: dict[str, Any] | None


def turn_from_recent(row: dict[str, Any]) -> Turn:
    """Normalize a /_contextlab/recent row (DB column names, raw timestamps)."""
    ts = row["ts"]
    t_first_byte = row.get("t_first_byte")
    t_done = row.get("t_done")
    return Turn(
        id=row["id"],
        ts=ts,
        session_id=row.get("session_id"),
        agent_id=row.get("agent_id"),
        model=row.get("model"),
        status=row.get("status") or 0,
        stop_reason=row.get("stop_reason"),
        input_tokens=row.get("input_tokens") or 0,
        output_tokens=row.get("output_tokens") or 0,
        cache_read=row.get("cache_read_tokens") or 0,
        cache_creation=row.get("cache_creation_tokens") or 0,
        ttft_s=round(t_first_byte - ts, 4) if t_first_byte else None,
        total_s=round(t_done - ts, 4) if t_done else None,
        metrics=row.get("metrics"),
    )


def turn_from_ws(event: dict[str, Any]) -> Turn:
    """Normalize a websocket event (nested usage block, precomputed latency)."""
    usage = event.get("usage") or {}
    latency = event.get("latency") or {}
    return Turn(
        id=event["id"],
        ts=event["ts"],
        session_id=event.get("session_id"),
        agent_id=event.get("agent_id"),
        model=event.get("model"),
        status=event.get("status") or 0,
        stop_reason=event.get("stop_reason"),
        input_tokens=usage.get("input_tokens") or 0,
        output_tokens=usage.get("output_tokens") or 0,
        cache_read=usage.get("cache_read_input_tokens") or 0,
        cache_creation=usage.get("cache_creation_input_tokens") or 0,
        ttft_s=latency.get("ttft_s"),
        total_s=latency.get("total_s"),
        metrics=event.get("metrics"),
    )


@dataclass
class TurnStore:
    """Ordered, deduped turn buffer merging backfill and live events."""

    turns: list[Turn] = field(default_factory=list)
    _ids: set[int] = field(default_factory=set)

    def add_backfill(self, backfill: list[Turn]) -> None:
        # Backfill wins for its own ids; live turns not in the backfill survive.
        seen = {t.id for t in backfill}
        live = [t for t in self.turns if t.id not in seen]
        merged = sorted([*backfill, *live], key=lambda t: t.id)[-MAX_TURNS:]
        self.turns = merged
        self._ids = {t.id for t in merged}

    def add_live(self, turn: Turn) -> None:
        if turn.id in self._ids:
            self.turns = [t for t in self.turns if t.id != turn.id]
        self.turns = sorted([*self.turns, turn], key=lambda t: t.id)[-MAX_TURNS:]
        self._ids = {t.id for t in self.turns}


# Fewer count_tokens samples than this and a correction factor is noisier
# than the bias it fixes — show the raw tiktoken figure instead.
MIN_SPOT_SAMPLES = 3


@dataclass(frozen=True)
class Kpis:
    spend: float
    savings: float
    hit_ratio: float
    turns: int
    unpriced: int
    hr_savings: float  # token-weighted would-be Headroom savings (raw tiktoken)
    hr_scored: int
    hr_spot_n: int = 0  # turns carrying count_tokens ground truth
    hr_factor: float | None = None  # measured drift factor (claude/tiktoken)
    hr_calibrated: float | None = None  # hr_savings × hr_factor, capped at 1


def aggregate(turns: list[Turn]) -> Kpis:
    """KPI roll-up, identical math to App.tsx: the hit ratio is token-weighted
    (sum of reads over sum of all input-side tokens), not a mean of per-turn
    ratios — and likewise Headroom savings are Σbefore−Σafter over Σbefore."""
    spend = savings = 0.0
    reads = inputs = priced = 0
    hr_before = hr_after = hr_scored = 0
    sc_tb = sc_ta = sc_cb = sc_ca = spot_n = 0
    for t in turns:
        reads += t.cache_read
        inputs += t.cache_read + t.cache_creation + t.input_tokens
        cost = (t.metrics or {}).get("cache", {}).get("cost_usd") if t.metrics else None
        if cost:
            spend += cost["total"]
            savings += cost["cache_savings"]
            priced += 1
        lens = (t.metrics or {}).get("headroom") if t.metrics else None
        if lens:
            hr_before += lens.get("tokens_before") or 0
            hr_after += lens.get("tokens_after") or 0
            hr_scored += 1
            spot = lens.get("spot_check") or {}
            if spot.get("claude_tokens_before"):
                spot_n += 1
                sc_tb += lens.get("tokens_before") or 0
                sc_ta += lens.get("tokens_after") or 0
                sc_cb += spot["claude_tokens_before"]
                sc_ca += spot.get("claude_tokens_after") or 0
    hr_savings = (hr_before - hr_after) / hr_before if hr_before else 0.0
    # Drift calibration: on the spot-checked pool, compare the savings ratio
    # under Claude's tokenizer vs tiktoken's, and scale the population figure
    # by that factor. Multiplicative, so a session where nothing fired stays
    # 0% — an additive offset would invent savings out of thin air.
    hr_factor = hr_calibrated = None
    if spot_n >= MIN_SPOT_SAMPLES and sc_tb and sc_cb:
        tik = (sc_tb - sc_ta) / sc_tb
        claude = (sc_cb - sc_ca) / sc_cb
        if tik > 0:
            hr_factor = claude / tik
            hr_calibrated = min(hr_savings * hr_factor, 1.0)
    return Kpis(
        spend=spend,
        savings=savings,
        hit_ratio=reads / inputs if inputs else 0.0,
        turns=len(turns),
        unpriced=len(turns) - priced,
        hr_savings=hr_savings,
        hr_scored=hr_scored,
        hr_spot_n=spot_n,
        hr_factor=hr_factor,
        hr_calibrated=hr_calibrated,
    )


def workdirs_by_session(turns: list[Turn]) -> dict[str, str]:
    """Latest known launch directory per session. Housekeeping calls carry no
    workdir marker, so turns fall back to their session's directory."""
    out: dict[str, str] = {}
    for t in turns:
        wd = (t.metrics or {}).get("workdir") if t.metrics else None
        if wd:
            out[t.session_id or "unknown"] = wd
    return out


def latest_session(turns: list[Turn]) -> str | None:
    """Session of the newest turn — what a live chart should follow (the
    busiest session can be a finished one that merely has more stored turns)."""
    return (turns[-1].session_id or "unknown") if turns else None


def sessions_by_count(turns: list[Turn]) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for t in turns:
        k = t.session_id or "unknown"
        counts[k] = counts.get(k, 0) + 1
    return sorted(counts.items(), key=lambda kv: -kv[1])


def filter_session(turns: list[Turn], session: str | None) -> list[Turn]:
    """session=None means all."""
    if session is None:
        return turns
    return [t for t in turns if (t.session_id or "unknown") == session]


# The web dashboard's 8 composition categories collapsed to 5: a terminal
# chart is ~14 half-cells tall, so categories under ~7% of a bar can't render
# — grouping keeps every bucket big enough to usually survive.
COMP_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("prompt", ("system", "tool_definitions")),
    ("user", ("user_text",)),
    ("assistant", ("assistant_text", "thinking", "tool_use")),
    ("tool results", ("tool_results",)),
)
COMP_OTHER = "other"


def composition_series(turns: list[Turn]) -> list[dict[str, int]]:
    """Per-turn composition bytes grouped per COMP_GROUPS (for the stacked
    chart); anything uncategorized lands in "other" so each row still sums to
    composition.total_bytes. Skips metric-less turns."""
    out = []
    for t in turns:
        comp = (t.metrics or {}).get("composition") if t.metrics else None
        if not comp or comp.get("total_bytes") is None:
            continue
        by_cat = comp.get("bytes") or {}
        row = {name: sum(by_cat.get(k) or 0 for k in keys) for name, keys in COMP_GROUPS}
        row[COMP_OTHER] = max(comp["total_bytes"] - sum(row.values()), 0)
        out.append(row)
    return out


def stack_heights(values: list[int], max_total: int, cells: int) -> list[int]:
    """Segment heights (in half-cells) for one zero-baselined stacked bar.
    Cumulative rounding: the bar's total height is round(total/max*cells)
    exactly, with no drift across segments — a sub-half-cell segment can
    vanish, but a nonzero bar never does (its largest segment gets 1 cell)."""
    if max_total <= 0 or cells <= 0:
        return [0] * len(values)
    heights, prev, cum = [], 0, 0
    for v in values:
        cum += v
        edge = round(cum / max_total * cells)
        heights.append(edge - prev)
        prev = edge
    if cum > 0 and prev == 0:
        heights[values.index(max(values))] = 1
    return heights


def latest_delta(turns: list[Turn]) -> dict[str, Any] | None:
    """Newest turn's delta block (new/resent bytes), if any."""
    for t in reversed(turns):
        delta = (t.metrics or {}).get("delta") if t.metrics else None
        if delta and not delta.get("first_turn"):
            return delta
    return None


# --- formatters (parity with dashboard/src/format.ts) ---


def fmt_bytes(n: float) -> str:
    if n >= 1_048_576:
        return f"{n / 1_048_576:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{int(n)} B"


def fmt_compact(n: float) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 10_000:
        return f"{n / 1000:.0f}K"
    if n >= 1000:
        return f"{n / 1000:.1f}K"
    return f"{int(n)}"


def fmt_usd(n: float) -> str:
    if n >= 1:
        return f"${n:.2f}"
    if n >= 0.01:
        return f"${n:.3f}"
    return f"${n:.4f}"


def fmt_pct(n: float) -> str:
    return f"{n * 100:.1f}%"


def fmt_time(ts: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(ts))


def short_model(model: str | None) -> str:
    if not model:
        return "?"
    return re.sub(r"-\d{8}$", "", model.removeprefix("claude-"))


def short_session(session_id: str | None) -> str:
    return session_id[:8] if session_id else "unknown"


def short_dir(path: str | None, cap: int = 10) -> str:
    """Last path segment, capped (default fits an ~86-column pane; the app
    raises the cap when the pane is wider)."""
    if not path:
        return "—"
    name = path.rstrip("/").rsplit("/", 1)[-1] or path
    return name if len(name) <= cap else name[: cap - 1] + "…"

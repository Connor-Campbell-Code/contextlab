"""Shadow cache ledger — prefix-survival simulation, one live turn at a time.

Per-request token deltas are the wrong ledger for any intervention on cached
traffic: with prompt caching, the stable prefix bills at 0.1x and rewriting
history re-bills everything downstream of the first changed byte at the
cache-write rate. The honest unit is cache-adjusted dollars, which can be
negative. This module answers two counterfactuals per request, both in
tokens (the proxy prices them):

  policy   — "if compression had been on since turn 1": prefix survival
             between consecutive *shadow* (compressed) requests.
  marginal — "if compression fired on THIS request, mid-session": prefix
             survival of the compressed request against the *raw* previous
             request, i.e. against what the real cache actually holds.

Input-side only: output tokens are identical in both ledgers by assumption —
a shadow can't know how compression would change the model's replies.

Pure module: no headroom, no tiktoken. The token counter is injected
(``count: Callable[[str], int]``) so the worker passes tiktoken and tests
pass anything cheap. Serialization is block-per-line:
json.dumps of a whole dict ends with "]}" and would never be a string
prefix of its successor, so append-only growth must serialize line-wise to
read as a literal prefix-extension.

The first serialized line encodes whether the tool list carries the
headroom_retrieve tool. Since headroom's PR-B7 ("session-sticky CCR tool
injection", v0.31.0) the tool stays registered for the rest of the session
once any compression has fired, so the flag here is sticky too: the first
firing still busts the whole conversation cache once (tool definitions sit
ahead of messages in the cacheable prefix — issue #809's mechanism), but
later not-fired turns no longer flip it back off. ShadowState.ever_fired
carries the stickiness between turns.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class ShadowState:
    """Previous turn's serializations for one context_key."""

    prev_raw: str | None = None
    prev_shadow: str | None = None
    ever_fired: bool = False  # PR-B7 stickiness: tool stays once injected
    post_reset: bool = False  # previous turn was a reset (warmup continues)


def _without_cache_control(msg: dict[str, Any]) -> dict[str, Any]:
    """Drop cache_control breakpoint markers before serializing. The real
    cache matches on content — markers only delimit segments, and clients
    move them to the tail every turn, so serializing them would read as an
    edit to the previous tail and bust the simulated prefix each turn."""
    content = msg.get("content")
    if not isinstance(content, list) or not any(
        isinstance(b, dict) and "cache_control" in b for b in content
    ):
        return msg
    return {
        **msg,
        "content": [
            {k: v for k, v in b.items() if k != "cache_control"} if isinstance(b, dict) else b
            for b in content
        ],
    }


def serialize(messages: list[dict[str, Any]], fired: bool) -> str:
    lines = [json.dumps({"tools_include_headroom_retrieve": fired})]
    lines += [json.dumps(_without_cache_control(m)) for m in messages]
    return "\n".join(lines) + "\n"


def shared_message_prefix(prev_raw: str, messages: list[dict[str, Any]]) -> int:
    """Count of leading messages unchanged since the previous raw request —
    the frozen prefix a cache-aware compressor must not rewrite (what the
    headroom proxy computes from its session tracker, reconstructed here
    from the stored serialization: line 0 is the flag line, each later line
    is one message)."""
    prev_lines = prev_raw.split("\n")[1:]
    shared = 0
    for prev_line, msg in zip(prev_lines, messages):
        if prev_line != json.dumps(_without_cache_control(msg)):
            break
        shared += 1
    return shared


def common_prefix_tokens(prev: str, cur: str, count: Callable[[str], int]) -> int:
    """Tokens of the longest common character prefix — an upper bound on the
    cacheable prefix (real caching is block-granular; generous to both
    ledgers equally)."""
    n = min(len(prev), len(cur))
    i = 0
    while i < n and prev[i] == cur[i]:
        i += 1
    return count(cur[:i])


def step(
    state: ShadowState,
    raw_messages: list[dict[str, Any]],
    shadow_messages: list[dict[str, Any]] | None,
    count: Callable[[str], int],
) -> tuple[ShadowState, dict[str, Any]]:
    """Advance one turn; shadow_messages is None when compress() didn't fire
    (the shadow conversation IS the raw one that turn)."""
    fired = shadow_messages is not None
    sticky = fired or state.ever_fired
    raw = serialize(raw_messages, fired=False)
    shadow = serialize(shadow_messages if fired else raw_messages, fired=sticky)
    total = count(shadow)
    reset = state.prev_shadow is None or state.prev_raw is None
    if reset:
        # First turn (or state lost): both ledgers pay a full write, so the
        # comparison stays fair.
        policy_prefix = marginal_prefix = 0
        extension = False
    else:
        policy_prefix = common_prefix_tokens(state.prev_shadow, shadow, count)
        extension = shadow.startswith(state.prev_shadow)
        marginal_prefix = common_prefix_tokens(state.prev_raw, shadow, count)
    ledger = {
        "policy": {
            "prefix_tokens": policy_prefix,
            "new_tokens": total - policy_prefix,
            "total_tokens": total,
            "prefix_extension": extension,
        },
        "marginal": {
            "prefix_tokens": marginal_prefix,
            "new_tokens": total - marginal_prefix,
            "total_tokens": total,
        },
        "fired": fired,
        "reset": reset,
        # Instrument boundary, not economics: on a reset the shadow world
        # re-pays a full cache write the real session never paid, and the
        # following turn can revert content the reset turn compressed (no
        # CCR replay store here). KPI aggregations must skip warmup turns.
        "warmup": reset or state.post_reset,
    }
    return ShadowState(prev_raw=raw, prev_shadow=shadow, ever_fired=sticky, post_reset=reset), ledger

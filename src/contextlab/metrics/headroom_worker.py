"""Headroom-lens sidecar worker — runs inside .venv-headroom.

The proxy venv deliberately does not depend on headroom (the instrument
stays clean of its subject); this script is executed by path with
.venv-headroom's interpreter and scores payloads over a JSON-lines pipe.

Protocol (one JSON object per line):
  stdin:  {"id": 7, "messages": [...], "model": "claude-...", "context_key": "..."}
  stdout: {"id": 7, "ok": true, "lens": {...}}
        | {"id": 7, "ok": false, "error": "..."}

When context_key is present the response's lens carries a "shadow" ledger
(see shadow_sim) — token-denominated prefix survival for the policy and
marginal counterfactuals. State is per context_key, in-memory, LRU-capped:
a worker restart resets the ledger (shadow_sim marks the turn reset=True).

Scoring: compress() over the whole message list (what the proxy would hand
Headroom), tiktoken o200k as the counter. Per-tool outcomes are derived by
diffing each tool_result block before vs after that SAME whole-conversation
pass, matched by tool_use_id — scoring a payload alone in a minimal wrapper
is wrong, because Headroom protects the most recent turns and an isolated
payload is always "recent" (it would read as protected even when the real
conversation compresses it). system/tools are not scored — compress() takes
messages only.

Frozen prefix: when the installed headroom supports it (the
feat/library-mode-frozen-prefix build), compress() receives
frozen_message_count = the count of leading messages unchanged since the
previous request in this conversation — modeling headroom's own proxy-mode
cache discipline instead of legacy rewrite-everything behavior. The count
is derived from the shadow state already kept per context_key, and is
reported in the lens as "frozen_message_count" (null when the capability
or a context_key is absent). Set CONTEXTLAB_FROZEN_PREFIX=0 to measure
legacy behavior instead.
"""

from __future__ import annotations

import json
import os
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Any

import tiktoken
from headroom import compress

try:
    from headroom.compress import CompressConfig

    _FROZEN_SUPPORTED = "frozen_message_count" in CompressConfig.__dataclass_fields__
except Exception:
    _FROZEN_SUPPORTED = False
_FROZEN_ENABLED = _FROZEN_SUPPORTED and os.environ.get("CONTEXTLAB_FROZEN_PREFIX", "1") != "0"

# Executed by path with .venv-headroom's interpreter, where contextlab isn't
# installed — import the sibling module by location.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import shadow_sim  # noqa: E402

ENC = tiktoken.get_encoding("o200k_base")
DEFAULT_MODEL = "claude-sonnet-4-5-20250929"
MAX_INDIVIDUAL = 8  # largest tool_results scored individually per request
MIN_CHARS = 400  # below this nothing plausibly fires; skip the work
MAX_CONTEXTS = 32  # LRU cap on per-conversation shadow state

_STATES: OrderedDict[str, shadow_sim.ShadowState] = OrderedDict()


def _count(text: str) -> int:
    return len(ENC.encode(text))


def _shadow(context_key: str, messages: list[dict], compressed: list[dict], fired: bool) -> dict:
    state = _STATES.pop(context_key, None) or shadow_sim.ShadowState()
    state, ledger = shadow_sim.step(state, messages, compressed if fired else None, _count)
    _STATES[context_key] = state
    while len(_STATES) > MAX_CONTEXTS:
        _STATES.popitem(last=False)
    return ledger


def _tokens(obj: Any) -> int:
    return len(ENC.encode(json.dumps(obj)))


def _compress(messages: list[dict], model: str, frozen_message_count: int = 0) -> Any:
    kwargs = {"frozen_message_count": frozen_message_count} if frozen_message_count else {}
    try:
        return compress([dict(m) for m in messages], model=model, **kwargs)
    except Exception:
        if model == DEFAULT_MODEL:
            raise
        return compress([dict(m) for m in messages], model=DEFAULT_MODEL, **kwargs)


def _tool_results_by_id(messages: list[dict]) -> tuple[dict[str, str], dict[str, Any]]:
    """tool_use_id -> tool name, and tool_use_id -> tool_result content."""
    names: dict[str, str] = {}
    contents: dict[str, Any] = {}
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                names[block.get("id", "")] = block.get("name", "?")
            elif block.get("type") == "tool_result":
                contents.setdefault(block.get("tool_use_id", ""), block.get("content"))
    return names, contents


def _score_tool_results(messages: list[dict], compressed: list[dict]) -> list[dict]:
    names, before_by_id = _tool_results_by_id(messages)
    _, after_by_id = _tool_results_by_id(compressed)

    rows = []
    for tid, payload in before_by_id.items():
        text = payload if isinstance(payload, str) else json.dumps(payload)
        if len(text) < MIN_CHARS:
            continue
        before = _tokens(payload)
        # Absent from the compressed conversation == dropped entirely.
        after = _tokens(after_by_id[tid]) if tid in after_by_id else 0
        savings = round(100 * (before - after) / before, 1) if before else 0.0
        rows.append(
            {
                "tool": names.get(tid, "?"),
                "bytes": len(text.encode("utf-8", errors="replace")),
                "tokens": before,
                "savings_pct": savings,
                "decision": "compressed" if after < before else "untouched",
            }
        )
    rows.sort(key=lambda r: -r["bytes"])
    return rows[:MAX_INDIVIDUAL]


def score(
    messages: list[dict],
    model: str | None,
    include_after: bool = False,
    context_key: str | None = None,
) -> dict:
    model = model or DEFAULT_MODEL
    frozen = None
    if _FROZEN_ENABLED and context_key:
        frozen = 0
        state = _STATES.get(context_key)
        if state and state.prev_raw:
            frozen = shadow_sim.shared_message_prefix(state.prev_raw, messages)
    result = _compress(messages, model, frozen or 0)
    before, after = _tokens(messages), _tokens(result.messages)
    savings = round(100 * (before - after) / before, 1) if before else 0.0
    transforms = [str(t) for t in result.transforms_applied]
    fired = after < before
    lens = {
        "tokens_before": before,
        "tokens_after": after,
        "savings_pct": savings,
        "fired": fired,
        "transforms": transforms,
        "frozen_message_count": frozen,
        "tool_results": _score_tool_results(messages, result.messages),
    }
    if context_key:
        lens["shadow"] = _shadow(context_key, messages, result.messages, fired)
    if include_after:
        # For the count_tokens spot-check. None when nothing fired: the
        # caller reuses the original messages instead of shipping a byte-
        # identical copy back over the pipe.
        lens["messages_after"] = result.messages if fired else None
    return lens


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req_id = None
        try:
            req = json.loads(line)
            req_id = req.get("id")
            lens = score(
                req.get("messages") or [],
                req.get("model"),
                bool(req.get("include_after")),
                req.get("context_key"),
            )
            out = {"id": req_id, "ok": True, "lens": lens}
        except Exception as exc:  # never die on one bad payload
            out = {"id": req_id, "ok": False, "error": f"{type(exc).__name__}: {exc}"}
        sys.stdout.write(json.dumps(out) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()

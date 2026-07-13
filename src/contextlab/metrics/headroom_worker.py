"""Headroom-lens sidecar worker — runs inside .venv-headroom.

The proxy venv deliberately does not depend on headroom (the instrument
stays clean of its subject); this script is executed by path with
.venv-headroom's interpreter and scores payloads over a JSON-lines pipe.

Protocol (one JSON object per line):
  stdin:  {"id": 7, "messages": [...], "model": "claude-..."}
  stdout: {"id": 7, "ok": true, "lens": {...}}
        | {"id": 7, "ok": false, "error": "..."}

Scoring: compress() over the whole message list (what the proxy would hand
Headroom), tiktoken o200k as the counter. Per-tool outcomes are derived by
diffing each tool_result block before vs after that SAME whole-conversation
pass, matched by tool_use_id — scoring a payload alone in a minimal wrapper
is wrong, because Headroom protects the most recent turns and an isolated
payload is always "recent" (it would read as protected even when the real
conversation compresses it). system/tools are not scored — compress() takes
messages only.
"""

from __future__ import annotations

import json
import sys
from typing import Any

import tiktoken
from headroom import compress

ENC = tiktoken.get_encoding("o200k_base")
DEFAULT_MODEL = "claude-sonnet-4-5-20250929"
MAX_INDIVIDUAL = 8  # largest tool_results scored individually per request
MIN_CHARS = 400  # below this nothing plausibly fires; skip the work


def _tokens(obj: Any) -> int:
    return len(ENC.encode(json.dumps(obj)))


def _compress(messages: list[dict], model: str) -> Any:
    try:
        return compress([dict(m) for m in messages], model=model)
    except Exception:
        if model == DEFAULT_MODEL:
            raise
        return compress([dict(m) for m in messages], model=DEFAULT_MODEL)


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


def score(messages: list[dict], model: str | None, include_after: bool = False) -> dict:
    model = model or DEFAULT_MODEL
    result = _compress(messages, model)
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
        "tool_results": _score_tool_results(messages, result.messages),
    }
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
                req.get("messages") or [], req.get("model"), bool(req.get("include_after"))
            )
            out = {"id": req_id, "ok": True, "lens": lens}
        except Exception as exc:  # never die on one bad payload
            out = {"id": req_id, "ok": False, "error": f"{type(exc).__name__}: {exc}"}
        sys.stdout.write(json.dumps(out) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()

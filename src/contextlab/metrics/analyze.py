"""Per-request context analysis — the metrics that token totals hide.

Everything here works on the raw Messages-API request JSON plus the usage
block the API returned. Token counts come from the API (billing ground
truth); the *composition* is measured in bytes, since only the server knows
per-segment tokenization. Byte fractions are a faithful proxy for "where is
the context going" without pretending to token-level precision (cf. Headroom
issue #809, where len//4 masquerades as tokens).
"""

from __future__ import annotations

import json
import re
from typing import Any

from . import pricing

# Claude Code embeds its launch directory in the system prompt's environment
# block ("Primary working directory: /path"). No header carries it, so the
# prompt text is the only source; housekeeping calls (title generation etc.)
# run a different system prompt and won't match.
_WORKDIR_RE = re.compile(r"[Ww]orking directory: (\S+)")

CATEGORIES = (
    "system",
    "tool_definitions",
    "user_text",
    "assistant_text",
    "tool_use",
    "tool_results",
    "thinking",
    "images",
    "other",
)


def _blob_len(value: Any) -> int:
    if isinstance(value, str):
        return len(value.encode("utf-8", errors="replace"))
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


def _content_blocks(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def composition(request: dict[str, Any]) -> dict[str, Any]:
    """Byte size of each context category, plus per-turn message counts."""
    sizes = dict.fromkeys(CATEGORIES, 0)
    sizes["system"] = _blob_len(request.get("system", ""))
    sizes["tool_definitions"] = _blob_len(request.get("tools", []))

    tool_use_names: dict[str, str] = {}
    tool_result_sizes: list[dict[str, Any]] = []
    for msg in request.get("messages", []):
        role = msg.get("role")
        for block in _content_blocks(msg.get("content")):
            btype = block.get("type")
            size = _blob_len(block)
            if btype == "text":
                sizes["user_text" if role == "user" else "assistant_text"] += size
            elif btype == "tool_use":
                sizes["tool_use"] += size
                tool_use_names[block.get("id", "")] = block.get("name", "?")
            elif btype == "tool_result":
                sizes["tool_results"] += size
                tool_result_sizes.append(
                    {"tool": tool_use_names.get(block.get("tool_use_id", ""), "?"),
                     "bytes": size}
                )
            elif btype in ("thinking", "redacted_thinking"):
                sizes["thinking"] += size
            elif btype == "image":
                sizes["images"] += size
            else:
                sizes["other"] += size

    total = sum(sizes.values())
    return {
        "bytes": sizes,
        "total_bytes": total,
        "fractions": {k: round(v / total, 4) if total else 0.0 for k, v in sizes.items()},
        "message_count": len(request.get("messages", [])),
        "tool_count": len(request.get("tools", []) or []),
        "tool_results": sorted(tool_result_sizes, key=lambda r: -r["bytes"])[:20],
    }


def workdir(request: dict[str, Any]) -> str | None:
    """The directory the client session was launched from, or None."""
    system = request.get("system") or ""
    if isinstance(system, list):
        system = "\n".join(b.get("text", "") for b in system if isinstance(b, dict))
    match = _WORKDIR_RE.search(system)
    return match.group(1) if match else None


def cache_economics(model: str | None, usage: dict[str, Any]) -> dict[str, Any]:
    """Hit ratio and dollars. The lazy metric is total tokens; the real
    question is how many of them were 0.1x cache reads vs full-price input."""
    read = usage.get("cache_read_input_tokens") or 0
    created = usage.get("cache_creation_input_tokens") or 0
    uncached = usage.get("input_tokens") or 0
    total_input = read + created + uncached
    return {
        "total_input_tokens": total_input,
        "cache_hit_ratio": round(read / total_input, 4) if total_input else 0.0,
        "cost_usd": pricing.turn_cost_usd(
            model, uncached, usage.get("output_tokens") or 0, read, created
        ),
    }


def turn_delta(request: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    """What changed since the last call in this session — how much of the
    context is being re-sent verbatim, and whether the cacheable prefix held."""
    msgs = request.get("messages", [])
    if previous is None:
        return {"first_turn": True, "messages_added": len(msgs)}
    prev_msgs = previous.get("messages", [])
    prefix_stable = _blob_len(request.get("system", "")) and request.get("system") == previous.get("system")
    tools_stable = request.get("tools") == previous.get("tools")

    shared = 0
    for a, b in zip(prev_msgs, msgs):
        if a == b:
            shared += 1
        else:
            break
    resent = sum(_blob_len(m) for m in msgs[:shared])
    new = sum(_blob_len(m) for m in msgs[shared:])
    return {
        "first_turn": False,
        "system_prompt_stable": bool(prefix_stable),
        "tools_stable": bool(tools_stable),  # False here busts the prompt cache
        "shared_message_prefix": shared,
        "messages_rewritten": max(len(prev_msgs) - shared, 0),
        "messages_added": len(msgs) - shared,
        "resent_bytes": resent,
        "new_bytes": new,
        "resent_fraction": round(resent / (resent + new), 4) if resent + new else 0.0,
    }


def analyze(
    request: dict[str, Any],
    usage: dict[str, Any],
    model: str | None,
    previous_request: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "composition": composition(request),
        "cache": cache_economics(model, usage),
        "delta": turn_delta(request, previous_request),
        "workdir": workdir(request),
    }

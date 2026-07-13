"""Tokenizer calibration spot-check against the API's count_tokens endpoint.

The headroom lens counts tokens with tiktoken o200k — a stand-in, since
Anthropic has never published Claude's tokenizer. Every Nth scored request
this module asks upstream POST /v1/messages/count_tokens (free, counted by
Claude's real tokenizer) for ground truth on the same messages-only payload,
reusing the sampled request's own auth headers so it works for both API keys
and subscription OAuth. Results ride along as metrics.headroom.spot_check;
the dashboard aggregates them into a measured error bar on the tile.

Same degradation contract as the lens: every failure returns None, and
MAX_FAILURES consecutive errors (auth rejected, endpoint missing upstream)
turn the spot-check off for the run.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

import httpx

log = logging.getLogger("contextlab.spot_check")

AUTH_HEADERS = ("x-api-key", "authorization", "anthropic-version", "anthropic-beta")
DEFAULT_EVERY = 25  # scored requests per spot-check
MAX_FAILURES = 3  # consecutive; then the spot-check turns itself off


class SpotCheck:
    def __init__(
        self,
        upstream: str,
        every: int | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if every is None:
            every = int(os.environ.get("CONTEXTLAB_SPOTCHECK_EVERY", DEFAULT_EVERY))
        self._every = every
        self._lock = threading.Lock()
        self._seen = 0
        self._failures = 0
        self._disabled = every <= 0
        self._client = httpx.Client(
            base_url=upstream, timeout=httpx.Timeout(10.0), transport=transport
        )

    @property
    def enabled(self) -> bool:
        return not self._disabled

    def due(self) -> bool:
        """Advance the sampling counter; True on the 1st and every Nth call."""
        if self._disabled:
            return False
        with self._lock:
            self._seen += 1
            return (self._seen - 1) % self._every == 0

    def _count(self, model: str, messages: list[dict], headers: dict[str, str]) -> int:
        resp = self._client.post(
            "/v1/messages/count_tokens",
            json={"model": model, "messages": messages},
            headers=headers,
        )
        resp.raise_for_status()
        return int(resp.json()["input_tokens"])

    def run(
        self,
        lens: dict[str, Any],
        req_json: dict[str, Any],
        request_headers: dict[str, str],
        messages_after: list[dict] | None,
    ) -> dict[str, Any] | None:
        """Claude-tokenizer ground truth for one scored request, or None.

        Counts the same messages-only payload the lens counted with tiktoken;
        `messages_after` is the compressed conversation when compression fired
        (None means unchanged, so the before-count is reused).
        """
        if self._disabled:
            return None
        model = req_json.get("model")
        messages = req_json.get("messages")
        headers = {k: request_headers[k] for k in AUTH_HEADERS if k in request_headers}
        if not model or not messages or not ("x-api-key" in headers or "authorization" in headers):
            return None
        try:
            claude_before = self._count(model, messages, headers)
            claude_after = (
                self._count(model, messages_after, headers)
                if lens.get("fired") and messages_after
                else claude_before
            )
        except Exception:
            log.exception("count_tokens spot-check failed")
            self._failures += 1
            if self._failures >= MAX_FAILURES:
                self._disabled = True
                log.warning(
                    "spot-check disabled after %d consecutive failures", self._failures
                )
            return None
        self._failures = 0
        tiktoken_before = lens.get("tokens_before") or 0
        return {
            "claude_tokens_before": claude_before,
            "claude_tokens_after": claude_after,
            "savings_pct_claude": round(100 * (claude_before - claude_after) / claude_before, 1)
            if claude_before
            else 0.0,
            # tiktoken's error relative to ground truth on the before-side
            "count_drift_pct": round(100 * (tiktoken_before - claude_before) / claude_before, 1)
            if claude_before
            else None,
        }

    def close(self) -> None:
        self._client.close()

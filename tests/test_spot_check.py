"""SpotCheck contract — sampling cadence, count_tokens math, degradation."""

from __future__ import annotations

import json

import httpx

from contextlab.metrics import spot_check
from contextlab.metrics.spot_check import SpotCheck

REQ = {"model": "claude-x", "messages": [{"role": "user", "content": "hi"}]}
AUTH = {"x-api-key": "sk-test", "anthropic-version": "2023-06-01"}
LENS = {"tokens_before": 110, "tokens_after": 110, "fired": False}


def counting_transport(counts: list[int], seen: list[dict]) -> httpx.MockTransport:
    """Each call pops the next count; the request body is appended to seen."""

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append({"headers": dict(request.headers), "body": json.loads(request.content)})
        return httpx.Response(200, json={"input_tokens": counts.pop(0)})

    return httpx.MockTransport(handler)


def test_due_samples_first_then_every_nth():
    sc = SpotCheck("http://up", every=3, transport=httpx.MockTransport(lambda r: None))
    assert [sc.due() for _ in range(7)] == [True, False, False, True, False, False, True]


def test_every_zero_disables():
    sc = SpotCheck("http://up", every=0, transport=httpx.MockTransport(lambda r: None))
    assert not sc.enabled
    assert not sc.due()
    assert sc.run(LENS, REQ, AUTH, None) is None


def test_unfired_turn_counts_once_and_reports_drift():
    seen: list[dict] = []
    sc = SpotCheck("http://up", every=1, transport=counting_transport([100], seen))
    out = sc.run(LENS, REQ, AUTH, None)
    assert out == {
        "claude_tokens_before": 100,
        "claude_tokens_after": 100,
        "savings_pct_claude": 0.0,
        "count_drift_pct": 10.0,  # tiktoken said 110 vs Claude's 100
    }
    assert len(seen) == 1  # nothing fired: before-count reused for after
    assert seen[0]["headers"]["x-api-key"] == "sk-test"
    assert seen[0]["body"] == {"model": "claude-x", "messages": REQ["messages"]}


def test_fired_turn_counts_compressed_messages_too():
    seen: list[dict] = []
    sc = SpotCheck("http://up", every=1, transport=counting_transport([100, 40], seen))
    lens = {"tokens_before": 110, "tokens_after": 44, "fired": True}
    compressed = [{"role": "user", "content": "hi (compressed)"}]
    out = sc.run(lens, REQ, AUTH, compressed)
    assert out is not None
    assert out["claude_tokens_after"] == 40
    assert out["savings_pct_claude"] == 60.0
    assert seen[1]["body"]["messages"] == compressed


def test_missing_auth_skips_without_burning_a_failure():
    sc = SpotCheck("http://up", every=1, transport=counting_transport([], []))
    assert sc.run(LENS, REQ, {"anthropic-version": "2023-06-01"}, None) is None
    assert sc.enabled


def test_upstream_errors_eventually_disable():
    transport = httpx.MockTransport(lambda r: httpx.Response(403, json={}))
    sc = SpotCheck("http://up", every=1, transport=transport)
    for _ in range(spot_check.MAX_FAILURES):
        assert sc.run(LENS, REQ, AUTH, None) is None
    assert not sc.enabled

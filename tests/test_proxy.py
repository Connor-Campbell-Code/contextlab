"""End-to-end proxy tests against a mock Anthropic upstream."""

from __future__ import annotations

import asyncio
import gzip
import json

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

from contextlab.proxy.app import create_app, parse_sse_usage
from contextlab.store.db import EventStore

USAGE_START = {"input_tokens": 3, "cache_creation_input_tokens": 1000,
               "cache_read_input_tokens": 2000, "output_tokens": 1}


def sse_body() -> bytes:
    events = [
        ("message_start", {"type": "message_start",
                           "message": {"model": "claude-sonnet-5", "usage": USAGE_START}}),
        ("content_block_delta", {"type": "content_block_delta",
                                 "delta": {"type": "text_delta", "text": "hi"}}),
        ("message_delta", {"type": "message_delta",
                           "delta": {"stop_reason": "end_turn"},
                           "usage": {"output_tokens": 42}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    return b"".join(
        f"event: {name}\ndata: {json.dumps(data)}\n\n".encode() for name, data in events
    )


def make_upstream() -> FastAPI:
    upstream = FastAPI()

    @upstream.post("/v1/messages")
    async def messages(request: Request):
        seen_headers["x-api-key"] = request.headers.get("x-api-key")
        seen_headers["anthropic-beta"] = request.headers.get("anthropic-beta")
        # Anthropic gzip-encodes SSE when the client advertises gzip; the
        # proxy must decode before forwarding (regression: gzip bytes were
        # forwarded with content-encoding stripped and the tee parsed nothing)
        return StreamingResponse(
            iter([gzip.compress(sse_body())]),
            media_type="text/event-stream",
            headers={"content-encoding": "gzip"},
        )

    return upstream


seen_headers: dict[str, str | None] = {}


class Server(uvicorn.Server):
    def install_signal_handlers(self) -> None:  # run inside pytest thread
        pass


async def serve(app: FastAPI, port: int) -> Server:
    server = Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    server._task = task
    return server


@pytest.mark.asyncio
async def test_streaming_passthrough_and_tee(tmp_path, monkeypatch):
    # Keep the headroom-lens sidecar out of proxy tests: point it at a path
    # that doesn't exist so the lens disables itself at construction.
    monkeypatch.setenv("CONTEXTLAB_HEADROOM_PY", str(tmp_path / "no-such-python"))
    upstream_srv = await serve(make_upstream(), 8971)
    store = EventStore(tmp_path / "test.db")
    proxy_srv = await serve(create_app("http://127.0.0.1:8971", store), 8972)
    try:
        request_body = {
            "model": "claude-sonnet-5",
            "system": "You are terse.",
            "tools": [{"name": "read_file", "input_schema": {"type": "object"}}],
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "http://127.0.0.1:8972/v1/messages?beta=true",
                json=request_body,
                headers={
                    "x-api-key": "sk-test",
                    "anthropic-beta": "oauth-cap",
                    "x-claude-code-session-id": "sess-1",
                },
            )
        # byte-identical passthrough, auth headers forwarded verbatim
        assert resp.status_code == 200
        assert resp.content == sse_body()
        assert seen_headers["x-api-key"] == "sk-test"
        assert seen_headers["anthropic-beta"] == "oauth-cap"

        # tee lands in sqlite with merged usage and metrics
        for _ in range(100):
            rows = store.recent()
            if rows:
                break
            await asyncio.sleep(0.05)
        assert rows, "tee never recorded the request"
        row = rows[0]
        assert row["session_id"] == "sess-1"
        assert row["input_tokens"] == 3
        assert row["output_tokens"] == 42  # message_delta wins over message_start
        assert row["cache_read_tokens"] == 2000
        assert row["stop_reason"] == "end_turn"
        comp = row["metrics"]["composition"]
        assert comp["bytes"]["system"] == len("You are terse.")
        assert comp["tool_count"] == 1
        assert row["metrics"]["cache"]["cache_hit_ratio"] == round(2000 / 3003, 4)
        cost = row["metrics"]["cache"]["cost_usd"]
        assert cost is not None and cost["cache_savings"] > 0
        assert row["metrics"]["delta"]["first_turn"] is True
    finally:
        upstream_srv.should_exit = True
        proxy_srv.should_exit = True
        await asyncio.gather(upstream_srv._task, proxy_srv._task)


def test_parse_sse_usage_merges_last_wins():
    usage, stop = parse_sse_usage(sse_body())
    assert usage["output_tokens"] == 42
    assert usage["cache_creation_input_tokens"] == 1000
    assert stop == "end_turn"

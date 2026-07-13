"""Transparent Anthropic-API reverse proxy with an observation tee.

Protocol obligations (code.claude.com/docs/en/llm-gateway-protocol):
- forward Authorization / x-api-key / anthropic-beta / anthropic-version
  verbatim — a subscription login's OAuth capability travels in anthropic-beta
- stream SSE through unbuffered; a buffering gateway stalls the client
- never rewrite request or error-response bodies

All analysis happens after bytes have been forwarded, off the event loop.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from ..metrics.analyze import analyze
from ..metrics.headroom_lens import HeadroomLens
from ..metrics.spot_check import SpotCheck
from ..store.db import EventStore

log = logging.getLogger("contextlab.proxy")

# Hop-by-hop or recomputed; everything else is forwarded verbatim.
SKIP_REQUEST_HEADERS = {"host", "content-length", "connection", "accept-encoding"}
SKIP_RESPONSE_HEADERS = {"content-length", "transfer-encoding", "connection", "content-encoding"}


def parse_sse_usage(body: bytes) -> tuple[dict[str, Any], str | None]:
    """Merge usage across message_start / message_delta events; last wins."""
    usage: dict[str, Any] = {}
    stop_reason = None
    for line in body.split(b"\n"):
        if not line.startswith(b"data:"):
            continue
        try:
            data = json.loads(line[5:].strip())
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if isinstance(data, dict):
            msg_usage = (data.get("message") or {}).get("usage") or data.get("usage")
            if isinstance(msg_usage, dict):
                usage.update({k: v for k, v in msg_usage.items() if v is not None})
            delta = data.get("delta")
            if isinstance(delta, dict) and delta.get("stop_reason"):
                stop_reason = delta["stop_reason"]
    return usage, stop_reason


def create_app(upstream: str, store: EventStore) -> FastAPI:
    client = httpx.AsyncClient(
        base_url=upstream,
        timeout=httpx.Timeout(connect=15.0, read=600.0, write=60.0, pool=15.0),
        limits=httpx.Limits(max_connections=64),
    )

    lens = HeadroomLens()
    spot = SpotCheck(upstream)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        lens.close()
        spot.close()
        await client.aclose()

    app = FastAPI(lifespan=lifespan)

    # Live dashboard, mounted before the catch-all proxy route so it wins.
    dist = Path(
        os.environ.get(
            "CONTEXTLAB_DASHBOARD",
            Path(__file__).resolve().parents[3] / "dashboard" / "dist",
        )
    )
    if dist.is_dir():
        # Registered before the mount: the slashless path otherwise reaches
        # StaticFiles with an empty relative path and 404s instead of
        # redirecting — /_contextlab/app must work as typed.
        @app.get("/_contextlab/app")
        async def dashboard_slash() -> RedirectResponse:
            return RedirectResponse("/_contextlab/app/")

        app.mount("/_contextlab/app", StaticFiles(directory=dist, html=True), name="dashboard")

    def record(
        *,
        request_headers: dict[str, str],
        request_body: bytes,
        response_body: bytes,
        status: int,
        streaming: bool,
        timings: dict[str, float],
    ) -> dict[str, Any] | None:
        """Runs in a worker thread: parse, analyze, persist. Never raises."""
        try:
            req_json = json.loads(request_body) if request_body else {}
        except json.JSONDecodeError:
            req_json = {}
        model = req_json.get("model")
        session_id = request_headers.get("x-claude-code-session-id")
        # Housekeeping calls (title generation, etc.) share the session id but
        # run a different system prompt; scope turn deltas to the conversation.
        context_key = (
            f"{session_id}:{hashlib.sha1(json.dumps(req_json.get('system', ''), sort_keys=True).encode()).hexdigest()[:8]}"
            if session_id
            else None
        )

        usage: dict[str, Any] = {}
        stop_reason = None
        if status == 200:
            if streaming:
                usage, stop_reason = parse_sse_usage(response_body)
            else:
                try:
                    resp_json = json.loads(response_body)
                    usage = resp_json.get("usage") or {}
                    stop_reason = resp_json.get("stop_reason")
                except json.JSONDecodeError:
                    pass

        previous = None
        prev_body = store.previous_request_body(context_key)
        if prev_body:
            try:
                previous = json.loads(prev_body)
            except json.JSONDecodeError:
                pass

        metrics = analyze(req_json, usage, model, previous) if req_json else None
        if metrics is not None and lens.enabled:
            sample = spot.enabled and spot.due()
            hr = lens.score(req_json.get("messages") or [], model, include_after=sample)
            if hr is not None:
                messages_after = hr.pop("messages_after", None)
                if sample:
                    hr["spot_check"] = spot.run(hr, req_json, request_headers, messages_after)
            metrics["headroom"] = hr
        row_id = store.insert_request(
            {
                "ts": timings["t_start"],
                "session_id": session_id,
                "context_key": context_key,
                "agent_id": request_headers.get("x-claude-code-agent-id"),
                "parent_agent_id": request_headers.get("x-claude-code-parent-agent-id"),
                "model": model,
                "status": status,
                "streaming": int(streaming),
                "stop_reason": stop_reason,
                "t_upstream_sent": timings.get("t_upstream_sent"),
                "t_first_byte": timings.get("t_first_byte"),
                "t_done": timings.get("t_done"),
                "input_tokens": usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens"),
                "cache_read_tokens": usage.get("cache_read_input_tokens"),
                "cache_creation_tokens": usage.get("cache_creation_input_tokens"),
                "metrics": metrics,
                "request_body": request_body,
                "response_body": response_body,
            }
        )
        return {
            "type": "request",
            "id": row_id,
            "ts": timings["t_start"],
            "session_id": session_id,
            "agent_id": request_headers.get("x-claude-code-agent-id"),
            "model": model,
            "status": status,
            "stop_reason": stop_reason,
            "usage": usage,
            "latency": {
                "ttft_s": round(timings["t_first_byte"] - timings["t_start"], 4)
                if timings.get("t_first_byte")
                else None,
                "total_s": round(timings["t_done"] - timings["t_start"], 4)
                if timings.get("t_done")
                else None,
            },
            "metrics": metrics,
        }

    async def record_and_publish(**kwargs: Any) -> None:
        try:
            event = await asyncio.to_thread(record, **kwargs)
            if event:
                store.publish(event)
        except Exception:
            log.exception("failed to record request")

    @app.get("/_contextlab/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "upstream": upstream}

    @app.get("/_contextlab/recent")
    async def recent(limit: int = 100) -> Any:
        return await asyncio.to_thread(store.recent, limit)

    @app.websocket("/_contextlab/ws")
    async def ws(websocket: WebSocket) -> None:
        await websocket.accept()
        q = store.subscribe()
        try:
            while True:
                event = await q.get()
                await websocket.send_json(event)
        except WebSocketDisconnect:
            pass
        finally:
            store.unsubscribe(q)

    @app.api_route(
        "/{path:path}",
        methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
    )
    async def proxy(request: Request, path: str) -> Response:
        t_start = time.time()
        body = await request.body()
        headers = {
            k: v for k, v in request.headers.items() if k.lower() not in SKIP_REQUEST_HEADERS
        }
        req_headers_lower = {k.lower(): v for k, v in request.headers.items()}

        upstream_req = client.build_request(
            request.method,
            f"/{path}",
            params=request.query_params,
            headers=headers,
            content=body,
        )
        try:
            t_upstream_sent = time.time()
            upstream_resp = await client.send(upstream_req, stream=True)
        except httpx.HTTPError as exc:
            return JSONResponse(
                {"type": "error", "error": {"type": "proxy_error", "message": str(exc)}},
                status_code=502,
            )

        resp_headers = {
            k: v
            for k, v in upstream_resp.headers.items()
            if k.lower() not in SKIP_RESPONSE_HEADERS
        }
        is_messages = path.rstrip("/").endswith("v1/messages")
        content_type = upstream_resp.headers.get("content-type", "")
        streaming = "text/event-stream" in content_type

        if not streaming:
            raw = await upstream_resp.aread()
            await upstream_resp.aclose()
            t_done = time.time()
            if is_messages:
                asyncio.create_task(
                    record_and_publish(
                        request_headers=req_headers_lower,
                        request_body=body,
                        response_body=raw,
                        status=upstream_resp.status_code,
                        streaming=False,
                        timings={
                            "t_start": t_start,
                            "t_upstream_sent": t_upstream_sent,
                            "t_first_byte": t_done,
                            "t_done": t_done,
                        },
                    )
                )
            return Response(raw, status_code=upstream_resp.status_code, headers=resp_headers)

        async def tee() -> AsyncIterator[bytes]:
            chunks: list[bytes] = []
            t_first_byte: float | None = None
            try:
                # aiter_bytes decodes any upstream content-encoding, matching
                # the stripped content-encoding header; aiter_raw would forward
                # gzip bytes mislabeled as plain and the SSE tee couldn't parse
                async for chunk in upstream_resp.aiter_bytes():
                    if t_first_byte is None:
                        t_first_byte = time.time()
                    chunks.append(chunk)
                    yield chunk  # forward before any processing — never buffer
            finally:
                await upstream_resp.aclose()
                if is_messages:
                    asyncio.create_task(
                        record_and_publish(
                            request_headers=req_headers_lower,
                            request_body=body,
                            response_body=b"".join(chunks),
                            status=upstream_resp.status_code,
                            streaming=True,
                            timings={
                                "t_start": t_start,
                                "t_upstream_sent": t_upstream_sent,
                                "t_first_byte": t_first_byte,
                                "t_done": time.time(),
                            },
                        )
                    )

        return StreamingResponse(
            tee(), status_code=upstream_resp.status_code, headers=resp_headers
        )

    return app

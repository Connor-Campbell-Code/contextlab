"""Event client for the TUI: backfill from /_contextlab/recent, then follow
the /_contextlab/ws push feed. Reconnects forever with the same backoff the
web dashboard uses (1s doubling to 15s, reset on a successful open); every
network error is swallowed into a state change so the hosting pane never dies.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from enum import Enum

import httpx
import websockets

from .model import Turn, turn_from_recent, turn_from_ws

RETRY_INITIAL_S = 1.0
RETRY_MAX_S = 15.0


class ConnState(Enum):
    CONNECTING = "connecting"
    LIVE = "live"
    RETRYING = "retrying"


class EventClient:
    """Feeds normalized Turns to callbacks; run() is a forever task."""

    def __init__(
        self,
        url: str,
        on_backfill: Callable[[list[Turn]], None],
        on_turn: Callable[[Turn], None],
        on_state: Callable[[ConnState], None],
        limit: int = 500,
    ) -> None:
        self._http_url = url.rstrip("/")
        self._ws_url = self._http_url.replace("https://", "wss://").replace("http://", "ws://")
        self._on_backfill = on_backfill
        self._on_turn = on_turn
        self._on_state = on_state
        self._limit = limit

    async def run(self) -> None:
        retry = RETRY_INITIAL_S
        self._on_state(ConnState.CONNECTING)
        while True:
            try:
                async with websockets.connect(
                    f"{self._ws_url}/_contextlab/ws", open_timeout=5, ping_interval=20
                ) as ws:
                    retry = RETRY_INITIAL_S
                    self._on_state(ConnState.LIVE)
                    # Backfill after the socket opens so no events fall in the
                    # gap; the store dedupes the overlap by id.
                    await self._backfill()
                    async for raw in ws:
                        event = json.loads(raw)
                        if event.get("type") == "request":
                            self._on_turn(turn_from_ws(event))
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            self._on_state(ConnState.RETRYING)
            await asyncio.sleep(retry)
            retry = min(retry * 2, RETRY_MAX_S)

    async def _backfill(self) -> None:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    f"{self._http_url}/_contextlab/recent", params={"limit": self._limit}
                )
                r.raise_for_status()
                rows = r.json()
        except Exception:
            return
        turns = sorted((turn_from_recent(row) for row in rows), key=lambda t: t.id)
        self._on_backfill(turns)

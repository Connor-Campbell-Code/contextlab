"""Recompute stored dollar figures after a pricing.PRICES change.

Costs are priced once, at ingest, and frozen into each row's metrics JSON —
so a missing or wrong rate keeps misreporting history after the table is
fixed. Usage tokens are stored as ground truth alongside, so every dollar
figure can be rebuilt from them: the turn's cache.cost_usd and the Headroom
shadow ledger's policy/marginal costs (which compare against it).

Safe against a live proxy: WAL mode, short per-chunk transactions, and only
rows whose numbers actually change are written.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from ..metrics import pricing
from ..proxy.app import price_shadow

CHUNK = 500


def reprice_metrics(model: str | None, tokens: tuple[int, int, int, int], metrics: dict[str, Any]) -> None:
    """Rebuild metrics' dollar fields in place from (input, output, cache_read,
    cache_creation) tokens. Stale figures are dropped first, so a model that
    has since become unpriced reads "unpriced" rather than keeping old dollars."""
    cache = metrics.get("cache")
    if not isinstance(cache, dict):
        return
    uncached, output, read, created = tokens
    cache["cost_usd"] = pricing.turn_cost_usd(
        model, uncached, output, read, created, pricing.write_multiplier(cache.get("write_ttl"))
    )
    shadow = (metrics.get("headroom") or {}).get("shadow")
    if shadow:
        for view in ("policy", "marginal"):
            v = shadow.get(view)
            if v:
                v.pop("cost_usd", None)
                v.pop("net_usd", None)
        price_shadow(shadow, model, cache)


def reprice(path: str | Path, dry_run: bool = False) -> dict[str, list[float]]:
    """Reprice every row. Returns {model: [rows, old_total, new_total]}."""
    conn = sqlite3.connect(str(path), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    summary: dict[str, list[float]] = {}
    pending: list[tuple[str, int]] = []
    rows = conn.execute(
        "SELECT id, model, input_tokens, output_tokens, cache_read_tokens,"
        " cache_creation_tokens, metrics FROM requests WHERE metrics IS NOT NULL"
    )
    for rid, model, it, ot, cr, cc, raw in rows:
        metrics = json.loads(raw)
        old = ((metrics.get("cache") or {}).get("cost_usd") or {}).get("total") or 0.0
        reprice_metrics(model, (it or 0, ot or 0, cr or 0, cc or 0), metrics)
        new = ((metrics.get("cache") or {}).get("cost_usd") or {}).get("total") or 0.0
        s = summary.setdefault(model or "?", [0, 0.0, 0.0])
        s[0] += 1
        s[1] += old
        s[2] += new
        updated = json.dumps(metrics)
        if updated != raw:
            pending.append((updated, rid))
    if not dry_run:
        for i in range(0, len(pending), CHUNK):
            with conn:
                conn.executemany("UPDATE requests SET metrics = ? WHERE id = ?", pending[i : i + CHUNK])
    conn.close()
    summary["_changed"] = [len(pending), 0.0, 0.0]
    return summary

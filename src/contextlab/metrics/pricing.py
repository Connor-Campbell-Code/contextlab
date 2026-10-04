"""Per-model pricing, USD per million tokens.

Cache multipliers per Anthropic docs (platform.claude.com/docs prompt-caching):
5-minute cache writes cost 1.25x base input, 1-hour writes 2x. Reads are 0.1x
on most models but per-model since Fable 5.1 (0.025x) and Opus 5.5 (0.05x).
Prices need manual upkeep; unknown models yield dollars=None rather than a
wrong number — which is why lookup() matches exact ids, never bare prefixes.
"""

from __future__ import annotations

CACHE_WRITE_5M = 1.25
CACHE_WRITE_1H = 2.0
CACHE_READ = 0.1


def write_multiplier(ttl: str | None) -> float:
    """Cache-write multiplier for a cache_control TTL ("5m", "1h", or None)."""
    return CACHE_WRITE_1H if ttl == "1h" else CACHE_WRITE_5M

# (input $/MTok, output $/MTok, cache-read multiplier), keyed by model id.
PRICES: dict[str, tuple[float, float, float]] = {
    "claude-fable-5-1": (10.0, 50.0, 0.025),
    "claude-mythos-5-1": (10.0, 50.0, 0.025),  # same model/pricing as fable-5-1
    "claude-fable-5": (10.0, 50.0, CACHE_READ),
    "claude-mythos-5": (10.0, 50.0, CACHE_READ),  # same model/pricing as fable-5
    "claude-opus-5-5": (4.0, 20.0, 0.05),
    "claude-opus-5": (5.0, 25.0, CACHE_READ),
    "claude-opus-4-8": (5.0, 25.0, CACHE_READ),
    "claude-opus-4-7": (5.0, 25.0, CACHE_READ),
    "claude-opus-4-6": (5.0, 25.0, CACHE_READ),
    "claude-opus-4-5": (5.0, 25.0, CACHE_READ),
    "claude-opus-4-1": (15.0, 75.0, CACHE_READ),
    "claude-opus-4-0": (15.0, 75.0, CACHE_READ),
    "claude-opus-4": (15.0, 75.0, CACHE_READ),  # claude-opus-4-20250514
    "claude-sonnet-5-5": (2.0, 10.0, CACHE_READ),
    "claude-sonnet-5": (2.0, 10.0, CACHE_READ),
    "claude-sonnet-4-6": (3.0, 15.0, CACHE_READ),
    "claude-sonnet-4-5": (3.0, 15.0, CACHE_READ),
    "claude-sonnet-4-0": (3.0, 15.0, CACHE_READ),
    "claude-sonnet-4": (3.0, 15.0, CACHE_READ),  # claude-sonnet-4-20250514
    "claude-haiku-4-5": (1.0, 5.0, CACHE_READ),
}


def lookup(model: str | None) -> tuple[float, float, float] | None:
    """Exact id, or id plus a dated snapshot suffix (claude-haiku-4-5-20251001).
    Not a bare prefix match: that priced claude-opus-5-5 as whatever
    claude-opus-5 was, and a future claude-opus-5-7 would silently inherit
    claude-opus-5-5's rates. An unlisted model should read "unpriced"."""
    if not model:
        return None
    if model in PRICES:
        return PRICES[model]
    base, _, suffix = model.rpartition("-")
    if suffix.isdigit() and len(suffix) == 8:
        return PRICES.get(base)
    return None


def shadow_input_cost_usd(
    model: str | None,
    prefix_tokens: int,
    new_tokens: int,
    cache_write_multiplier: float = CACHE_WRITE_5M,
) -> float | None:
    """Ideal-cache input cost of a simulated request: the surviving prefix
    bills as reads, everything past it as writes (shadow_sim's model).
    Input side only — a shadow ledger can't know the counterfactual output."""
    prices = lookup(model)
    if prices is None:
        return None
    in_price = prices[0] / 1_000_000
    return round(
        prefix_tokens * in_price * prices[2] + new_tokens * in_price * cache_write_multiplier,
        6,
    )


def turn_cost_usd(
    model: str | None,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_creation_tokens: int,
    cache_write_multiplier: float = CACHE_WRITE_5M,
) -> dict[str, float] | None:
    """Dollar breakdown of one API call, and what the same call would have
    cost with no caching at all — the difference is the cache's real value."""
    prices = lookup(model)
    if prices is None:
        return None
    in_price, out_price, read_mult = prices
    per = 1_000_000
    uncached = input_tokens * in_price / per
    writes = cache_creation_tokens * in_price * cache_write_multiplier / per
    reads = cache_read_tokens * in_price * read_mult / per
    output = output_tokens * out_price / per
    total = uncached + writes + reads + output
    no_cache_world = (
        (input_tokens + cache_read_tokens + cache_creation_tokens) * in_price / per
        + output
    )
    return {
        "uncached_input": round(uncached, 6),
        "cache_writes": round(writes, 6),
        "cache_reads": round(reads, 6),
        "output": round(output, 6),
        "total": round(total, 6),
        "no_cache_total": round(no_cache_world, 6),
        "cache_savings": round(no_cache_world - total, 6),
    }

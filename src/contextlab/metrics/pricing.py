"""Per-model pricing, USD per million tokens.

Cache multipliers per Anthropic docs (platform.claude.com/docs prompt-caching):
5-minute cache writes cost 1.25x base input, 1-hour writes 2x, reads 0.1x.
Prices need manual upkeep; unknown models yield dollars=None rather than a
wrong number.
"""

from __future__ import annotations

CACHE_WRITE_5M = 1.25
CACHE_WRITE_1H = 2.0
CACHE_READ = 0.1


def write_multiplier(ttl: str | None) -> float:
    """Cache-write multiplier for a cache_control TTL ("5m", "1h", or None)."""
    return CACHE_WRITE_1H if ttl == "1h" else CACHE_WRITE_5M

# (input $/MTok, output $/MTok), matched by longest prefix of the model id.
PRICES: dict[str, tuple[float, float]] = {
    "claude-fable-5": (10.0, 50.0),
    "claude-mythos-5": (10.0, 50.0),  # same model/pricing as fable-5
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4": (15.0, 75.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-sonnet-4": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


def lookup(model: str | None) -> tuple[float, float] | None:
    if not model:
        return None
    best = None
    for prefix, prices in PRICES.items():
        if model.startswith(prefix) and (best is None or len(prefix) > len(best[0])):
            best = (prefix, prices)
    return best[1] if best else None


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
    in_price, out_price = prices
    per = 1_000_000
    uncached = input_tokens * in_price / per
    writes = cache_creation_tokens * in_price * cache_write_multiplier / per
    reads = cache_read_tokens * in_price * CACHE_READ / per
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

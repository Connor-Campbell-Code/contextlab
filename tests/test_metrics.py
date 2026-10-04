from pytest import approx

from contextlab.metrics.analyze import composition, turn_delta
from contextlab.metrics.pricing import shadow_input_cost_usd, turn_cost_usd


def _req(n_turns: int, tools=None):
    msgs = []
    for i in range(n_turns):
        msgs.append({"role": "user", "content": f"question {i}"})
        msgs.append({
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": f"t{i}", "name": "read_file", "input": {"p": "x"}},
            ],
        })
        msgs.append({
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": f"t{i}", "content": "data " * 100},
            ],
        })
    return {"model": "claude-sonnet-4-6", "system": "sys", "tools": tools or [], "messages": msgs}


def test_composition_attributes_tool_results_to_tool_names():
    comp = composition(_req(2))
    assert comp["bytes"]["tool_results"] > comp["bytes"]["user_text"]
    assert comp["tool_results"][0]["tool"] == "read_file"
    assert abs(sum(comp["fractions"].values()) - 1.0) < 0.01


def test_turn_delta_detects_stable_prefix_and_tool_churn():
    prev, cur = _req(1), _req(2)
    d = turn_delta(cur, prev)
    assert d["system_prompt_stable"] and d["tools_stable"]
    assert d["shared_message_prefix"] == 3 and d["messages_added"] == 3
    assert 0 < d["resent_fraction"] < 1

    # fluctuating tool list (Headroom issue #809's cache-bust) must be flagged
    cur_churned = _req(2, tools=[{"name": "retrieve_compressed"}])
    assert turn_delta(cur_churned, prev)["tools_stable"] is False


def test_cache_pricing_multipliers():
    cost = turn_cost_usd("claude-sonnet-4-6", input_tokens=0, output_tokens=0,
                         cache_read_tokens=1_000_000, cache_creation_tokens=1_000_000)
    assert cost["cache_reads"] == approx(3.0 * 0.1)
    assert cost["cache_writes"] == approx(3.0 * 1.25)
    assert cost["cache_savings"] == approx(6.0 - cost["total"])
    assert turn_cost_usd("unknown-model", 1, 1, 1, 1) is None

    from contextlab.metrics.pricing import CACHE_WRITE_1H, write_multiplier

    cost_1h = turn_cost_usd("claude-sonnet-4-6", 0, 0, 0, 1_000_000,
                            cache_write_multiplier=CACHE_WRITE_1H)
    assert cost_1h["cache_writes"] == approx(3.0 * 2.0)
    assert write_multiplier("1h") == 2.0
    assert write_multiplier("5m") == 1.25
    assert write_multiplier(None) == 1.25


def test_cache_write_ttl_detected_anywhere_in_request():
    from contextlab.metrics.analyze import cache_economics, cache_write_ttl

    assert cache_write_ttl(_req(1)) is None  # no breakpoints at all
    # ttl-less breakpoint means the 5-minute default
    req_5m = _req(1, tools=[{"name": "t", "cache_control": {"type": "ephemeral"}}])
    assert cache_write_ttl(req_5m) == "5m"
    # 1h on a system block (where Claude Code puts it) wins over 5m elsewhere
    req_1h = _req(1, tools=[{"name": "t", "cache_control": {"type": "ephemeral"}}])
    req_1h["system"] = [
        {"type": "text", "text": "sys",
         "cache_control": {"type": "ephemeral", "ttl": "1h"}},
    ]
    assert cache_write_ttl(req_1h) == "1h"
    # breakpoints on message content blocks are seen too
    req_msg = _req(1)
    req_msg["messages"][-1]["content"][0]["cache_control"] = {"type": "ephemeral", "ttl": "1h"}
    assert cache_write_ttl(req_msg) == "1h"

    # the ttl flows through cache_economics into the write price
    usage = {"cache_creation_input_tokens": 1_000_000}
    econ = cache_economics("claude-sonnet-4-6", usage, write_ttl="1h")
    assert econ["write_ttl"] == "1h"
    assert econ["cost_usd"]["cache_writes"] == approx(3.0 * 2.0)
    assert cache_economics("claude-sonnet-4-6", usage)["cost_usd"]["cache_writes"] == approx(3.0 * 1.25)


def test_shadow_input_cost_reads_prefix_writes_the_rest():
    # 1M prefix tokens at 0.1x + 1M new tokens at 1.25x, sonnet input $3/MTok.
    cost = shadow_input_cost_usd("claude-sonnet-4-6", prefix_tokens=1_000_000, new_tokens=1_000_000)
    assert cost == approx(3.0 * 0.1 + 3.0 * 1.25)
    assert shadow_input_cost_usd("unknown-model", 1, 1) is None


def test_price_shadow_attaches_dollars_and_marginal_net():
    from contextlab.proxy.app import price_shadow

    shadow = {
        "policy": {"prefix_tokens": 1_000_000, "new_tokens": 0, "total_tokens": 1_000_000},
        "marginal": {"prefix_tokens": 0, "new_tokens": 1_000_000, "total_tokens": 1_000_000},
        "fired": True,
        "reset": False,
    }
    cache = {
        "cost_usd": turn_cost_usd(
            "claude-sonnet-4-6",
            input_tokens=0,
            output_tokens=0,
            cache_read_tokens=1_000_000,
            cache_creation_tokens=0,
        )
    }
    price_shadow(shadow, "claude-sonnet-4-6", cache)
    assert shadow["policy"]["cost_usd"] == approx(0.3)  # all reads
    assert shadow["marginal"]["cost_usd"] == approx(3.75)  # all writes
    # Actual input billed $0.30 (all cache reads); firing now would cost $3.75
    # — the net is NEGATIVE: the busted cache outweighs the tokens saved.
    assert shadow["marginal"]["net_usd"] == approx(0.3 - 3.75)

    # With a 1h write_ttl in the cache metrics, shadow writes price at 2x.
    shadow_1h = {
        "policy": {"prefix_tokens": 0, "new_tokens": 1_000_000, "total_tokens": 1_000_000},
        "marginal": None,
        "fired": True,
        "reset": False,
    }
    price_shadow(shadow_1h, "claude-sonnet-4-6", {**cache, "write_ttl": "1h"})
    assert shadow_1h["policy"]["cost_usd"] == approx(3.0 * 2.0)

    # Unknown model: no dollars attached, no net.
    bare = {
        "policy": {"prefix_tokens": 1, "new_tokens": 1, "total_tokens": 2},
        "marginal": {"prefix_tokens": 1, "new_tokens": 1, "total_tokens": 2},
        "fired": False,
        "reset": False,
    }
    price_shadow(bare, "unknown-model", {"cost_usd": None})
    assert "cost_usd" not in bare["policy"]
    assert "net_usd" not in bare["marginal"]
    price_shadow(None, "claude-sonnet-4-6", cache)  # absent shadow is a no-op


def test_workdir_parsed_from_string_and_block_system_prompts():
    from contextlab.metrics.analyze import workdir

    assert workdir(
        {"system": "You are...\n - Primary working directory: /home/u/projects/app\n"}
    ) == "/home/u/projects/app"
    assert workdir(
        {"system": [{"type": "text", "text": "Working directory: /tmp/x"}]}
    ) == "/tmp/x"
    assert workdir({"system": "no marker here"}) is None
    assert workdir({}) is None


def test_per_model_cache_read_rates():
    reads = lambda m: turn_cost_usd(m, 0, 0, 1_000_000, 0)["cache_reads"]  # noqa: E731
    assert reads("claude-opus-5-5") == approx(4.0 * 0.05)
    assert reads("claude-fable-5-1") == approx(10.0 * 0.025)
    assert reads("claude-fable-5") == approx(10.0 * 0.1)
    assert reads("claude-sonnet-5-5") == approx(2.0 * 0.1)
    assert shadow_input_cost_usd("claude-opus-5-5", 1_000_000, 0) == approx(0.2)


def test_price_lookup_is_exact_not_prefix():
    from contextlab.metrics.pricing import lookup

    assert lookup("claude-opus-5-5")[:2] == (4.0, 20.0)
    assert lookup("claude-opus-5")[:2] == (5.0, 25.0)
    assert lookup("claude-sonnet-5")[:2] == (2.0, 10.0)
    assert lookup("claude-opus-4-7")[:2] == (5.0, 25.0)  # not opus-4's $15
    # dated snapshots resolve to their alias
    assert lookup("claude-haiku-4-5-20251001")[:2] == (1.0, 5.0)
    assert lookup("claude-opus-4-20250514")[:2] == (15.0, 75.0)
    # an unlisted sibling is unpriced, not silently given a neighbour's rates
    assert lookup("claude-opus-5-7") is None
    assert lookup("claude-opus-5-5-preview") is None


from pytest import approx

from contextlab.metrics.analyze import composition, turn_delta
from contextlab.metrics.pricing import turn_cost_usd


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
    return {"model": "claude-sonnet-5", "system": "sys", "tools": tools or [], "messages": msgs}


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
    cost = turn_cost_usd("claude-sonnet-5", input_tokens=0, output_tokens=0,
                         cache_read_tokens=1_000_000, cache_creation_tokens=1_000_000)
    assert cost["cache_reads"] == approx(3.0 * 0.1)
    assert cost["cache_writes"] == approx(3.0 * 1.25)
    assert cost["cache_savings"] == approx(6.0 - cost["total"])
    assert turn_cost_usd("unknown-model", 1, 1, 1, 1) is None

    from contextlab.metrics.pricing import CACHE_WRITE_1H, write_multiplier

    cost_1h = turn_cost_usd("claude-sonnet-5", 0, 0, 0, 1_000_000,
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
    econ = cache_economics("claude-sonnet-5", usage, write_ttl="1h")
    assert econ["write_ttl"] == "1h"
    assert econ["cost_usd"]["cache_writes"] == approx(3.0 * 2.0)
    assert cache_economics("claude-sonnet-5", usage)["cost_usd"]["cache_writes"] == approx(3.0 * 1.25)


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

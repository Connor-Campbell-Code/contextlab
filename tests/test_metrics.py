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

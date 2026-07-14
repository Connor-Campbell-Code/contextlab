"""Shadow cache ledger mechanics — experiment 04's prefix-survival model,
one turn at a time, with an injected char-count "tokenizer" so nothing here
needs tiktoken or headroom."""

from __future__ import annotations

import json

from contextlab.metrics.shadow_sim import (
    ShadowState,
    common_prefix_tokens,
    serialize,
    shared_message_prefix,
    step,
)

COUNT = len  # 1 token per char: prefix arithmetic stays exact

FLAG_LEN = len(json.dumps({"tools_include_headroom_retrieve": False}))

M1 = [{"role": "user", "content": "checkout latency is spiking, investigate"}]
M2 = M1 + [{"role": "assistant", "content": "reading logs " * 20}]
M3 = M2 + [{"role": "user", "content": "and the payments service?"}]


def test_first_turn_resets_full_write():
    _, ledger = step(ShadowState(), M1, None, COUNT)
    total = COUNT(serialize(M1, fired=False))
    assert ledger["reset"] is True
    assert ledger["fired"] is False
    assert ledger["policy"] == {
        "prefix_tokens": 0,
        "new_tokens": total,
        "total_tokens": total,
        "prefix_extension": False,
    }
    assert ledger["marginal"]["prefix_tokens"] == 0


def test_append_only_growth_survives_as_prefix():
    state, _ = step(ShadowState(), M1, None, COUNT)
    _, ledger = step(state, M2, None, COUNT)
    prev, cur = serialize(M1, fired=False), serialize(M2, fired=False)
    assert cur.startswith(prev)
    assert ledger["reset"] is False
    assert ledger["policy"]["prefix_extension"] is True
    assert ledger["policy"]["prefix_tokens"] == COUNT(prev)
    assert ledger["policy"]["new_tokens"] == COUNT(cur) - COUNT(prev)
    # Nothing fired, so the marginal counterfactual is the same request.
    assert ledger["marginal"]["prefix_tokens"] == COUNT(prev)


def test_no_fire_turns_advance_shadow_state_as_raw():
    state, _ = step(ShadowState(), M1, None, COUNT)
    assert state.prev_shadow == state.prev_raw == serialize(M1, fired=False)


def test_fire_flip_busts_the_whole_prefix():
    # Turn 1 unfired, turn 2 fired with byte-identical messages: the #809
    # tool-injection line flips, so the surviving prefix can't even cover
    # the flag line — the conversation cache is gone.
    state, _ = step(ShadowState(), M2, None, COUNT)
    _, ledger = step(state, M3, M3, COUNT)
    assert ledger["fired"] is True
    assert ledger["policy"]["prefix_extension"] is False
    assert ledger["policy"]["prefix_tokens"] < FLAG_LEN


def test_consecutive_fires_diverge_at_the_gut_point():
    # Both turns fired (flag line identical); turn 2's compression rewrites
    # the FIRST message (read_lifecycle:stale pattern) — the prefix survives
    # the flag line but dies inside message 1, busting everything after.
    shadow2 = [{"role": "user", "content": "[gutted]"}, *M3[1:]]
    state, _ = step(ShadowState(), M2, M2, COUNT)
    _, ledger = step(state, M3, shadow2, COUNT)
    prefix = ledger["policy"]["prefix_tokens"]
    assert FLAG_LEN <= prefix < FLAG_LEN + 1 + COUNT(json.dumps(M2[0]))
    assert ledger["policy"]["prefix_extension"] is False


def test_marginal_compares_against_previous_raw_request():
    # Policy view: turn 1 fired, so turn 2 (also fired, append-only shadow)
    # extends the shadow prefix. Marginal view: the REAL cache holds the raw
    # unfired request, so the same turn-2 request busts at the flag line.
    state, _ = step(ShadowState(), M2, M2, COUNT)
    _, ledger = step(state, M3, M3, COUNT)
    assert ledger["policy"]["prefix_extension"] is True
    assert ledger["policy"]["prefix_tokens"] == COUNT(serialize(M2, fired=True))
    assert ledger["marginal"]["prefix_tokens"] < FLAG_LEN


def test_moving_cache_control_marker_is_not_an_edit():
    # Claude Code moves the cache_control breakpoint to the newest tail block
    # every turn. The real cache matches on content and ignores the marker,
    # so the simulation must too — with it serialized, every turn of an
    # append-only conversation would falsely bust at the old marker position.
    def mark_tail(messages):
        marked = [dict(m) for m in messages]
        last = dict(marked[-1])
        last["content"] = [
            {"type": "text", "text": last["content"],
             "cache_control": {"type": "ephemeral", "ttl": "1h"}},
        ]
        marked[-1] = last
        return marked

    def as_blocks(m):
        return {**m, "content": [{"type": "text", "text": m["content"]}]}

    turn1 = mark_tail(M1)
    turn2 = [as_blocks(M2[0]), *mark_tail(M2[1:])]  # marker moved off msg 1
    state, _ = step(ShadowState(), turn1, None, COUNT)
    _, ledger = step(state, turn2, None, COUNT)
    assert ledger["policy"]["prefix_extension"] is True
    assert ledger["policy"]["prefix_tokens"] == COUNT(serialize(turn1, fired=False))


def test_warmup_covers_reset_and_the_following_turn():
    state, ledger1 = step(ShadowState(), M1, None, COUNT)
    assert ledger1["reset"] is True and ledger1["warmup"] is True
    state, ledger2 = step(state, M2, None, COUNT)
    assert ledger2["reset"] is False and ledger2["warmup"] is True
    _, ledger3 = step(state, M3, None, COUNT)
    assert ledger3["warmup"] is False


def test_tool_flag_is_sticky_after_first_fire():
    # PR-B7 modeling: turn 1 fired (tool injected, busts once), turn 2 NOT
    # fired — the tool stays registered, so the flag line doesn't flip back
    # and an append-only turn extends the shadow prefix instead of busting.
    state, _ = step(ShadowState(), M2, M2, COUNT)
    state, ledger = step(state, M3, None, COUNT)
    assert ledger["fired"] is False
    assert ledger["policy"]["prefix_extension"] is True
    assert ledger["policy"]["prefix_tokens"] == COUNT(serialize(M2, fired=True))
    assert state.ever_fired is True


def test_shared_message_prefix_counts_unchanged_leading_messages():
    prev_raw = serialize(M2, fired=False)
    # Pure append: every previous message is frozen.
    assert shared_message_prefix(prev_raw, M3) == len(M2)
    # Identical request: all messages frozen, trailing flag line ignored.
    assert shared_message_prefix(prev_raw, M2) == len(M2)
    # History rewritten at message 0 (compaction): nothing is frozen.
    assert shared_message_prefix(prev_raw, [{"role": "user", "content": "[summary]"}, *M3[1:]]) == 0
    # A moved cache_control marker is not an edit (mirrors serialize()).
    marked = [dict(M2[0]), dict(M2[1])]
    marked[1]["content"] = [
        {"type": "text", "text": M2[1]["content"],
         "cache_control": {"type": "ephemeral", "ttl": "1h"}},
    ]
    prev_marked = serialize(marked, fired=False)
    unmarked_now = [marked[0], {**marked[1], "content": [{"type": "text", "text": M2[1]["content"]}]}]
    assert shared_message_prefix(prev_marked, unmarked_now) == 2


def test_common_prefix_tokens_counts_the_shared_region():
    count_words = lambda s: len(s.split())
    assert common_prefix_tokens("a b c d", "a b c x", count_words) == 3  # "a b c "
    assert common_prefix_tokens("xyz", "abc", len) == 0
    assert common_prefix_tokens("same", "same", len) == 4

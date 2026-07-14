"""The TUI's data layer must agree with the web dashboard's client-side math
(useEvents.ts normalization, App.tsx KPI aggregation) on the same inputs."""

from contextlab.tui.model import (
    Kpis,
    TurnStore,
    aggregate,
    composition_series,
    filter_session,
    fmt_usd,
    latest_delta,
    latest_session,
    sessions_by_count,
    short_model,
    short_session,
    stack_heights,
    turn_from_recent,
    turn_from_ws,
)

METRICS = {
    "composition": {"total_bytes": 40_000},
    "cache": {
        "cache_hit_ratio": 0.9,
        "cost_usd": {"total": 0.05, "no_cache_total": 0.30, "cache_savings": 0.25},
    },
    "delta": {"first_turn": False, "new_bytes": 2_000, "resent_bytes": 38_000, "resent_fraction": 0.95},
}


def recent_row(**over):
    row = {
        "id": 1,
        "ts": 1000.0,
        "session_id": "abcdef12-3456",
        "agent_id": None,
        "model": "claude-opus-4-8-20260101",
        "status": 200,
        "stop_reason": "end_turn",
        "t_upstream_sent": 1000.1,
        "t_first_byte": 1001.5,
        "t_done": 1003.25,
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_read_tokens": 900,
        "cache_creation_tokens": 0,
        "metrics": METRICS,
    }
    row.update(over)
    return row


def ws_event(**over):
    event = {
        "type": "request",
        "id": 1,
        "ts": 1000.0,
        "session_id": "abcdef12-3456",
        "agent_id": None,
        "model": "claude-opus-4-8-20260101",
        "status": 200,
        "stop_reason": "end_turn",
        "usage": {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_input_tokens": 900,
            "cache_creation_input_tokens": 0,
        },
        "latency": {"ttft_s": 1.5, "total_s": 3.25},
        "metrics": METRICS,
    }
    event.update(over)
    return event


def test_recent_and_ws_shapes_normalize_identically():
    assert turn_from_recent(recent_row()) == turn_from_ws(ws_event())


def test_ttft_derived_from_timestamps():
    t = turn_from_recent(recent_row(t_first_byte=1002.0, t_done=None))
    assert t.ttft_s == 2.0
    assert t.total_s is None


def test_recent_null_token_columns_default_to_zero():
    t = turn_from_recent(recent_row(input_tokens=None, cache_read_tokens=None))
    assert t.input_tokens == 0
    assert t.cache_read == 0


def test_aggregate_hit_ratio_is_token_weighted_not_mean():
    # Turn A: tiny, 100% hit. Turn B: huge, 0% hit. Mean of ratios would be
    # 50%; token-weighted (what App.tsx computes) is 10/10010.
    a = turn_from_recent(recent_row(id=1, input_tokens=0, cache_read_tokens=10, cache_creation_tokens=0))
    b = turn_from_recent(recent_row(id=2, input_tokens=10_000, cache_read_tokens=0, cache_creation_tokens=0))
    k = aggregate([a, b])
    assert abs(k.hit_ratio - 10 / 10_010) < 1e-9
    assert k.hit_ratio < 0.01  # nowhere near the naive 0.5


def test_aggregate_spend_savings_and_unpriced():
    priced = turn_from_recent(recent_row(id=1))
    unpriced_metrics = {**METRICS, "cache": {"cache_hit_ratio": 0.0, "cost_usd": None}}
    unpriced = turn_from_recent(recent_row(id=2, model="some-local-model", metrics=unpriced_metrics))
    no_metrics = turn_from_recent(recent_row(id=3, metrics=None))
    k = aggregate([priced, unpriced, no_metrics])
    assert k == Kpis(
        spend=0.05, savings=0.25, hit_ratio=k.hit_ratio, turns=3, unpriced=2,
        hr_savings=0.0, hr_scored=0,
    )


def test_aggregate_headroom_is_token_weighted():
    # Turn A: big, 25% saved. Turn B: small, 0%. Token-weighted (App.tsx math)
    # is (100k−80k)/100k over the pooled sums, not a mean of per-turn ratios.
    a_metrics = {**METRICS, "headroom": {"tokens_before": 90_000, "tokens_after": 70_000}}
    b_metrics = {**METRICS, "headroom": {"tokens_before": 10_000, "tokens_after": 10_000}}
    a = turn_from_recent(recent_row(id=1, metrics=a_metrics))
    b = turn_from_recent(recent_row(id=2, metrics=b_metrics))
    unscored = turn_from_recent(recent_row(id=3))  # METRICS has no headroom key
    k = aggregate([a, b, unscored])
    assert k.hr_scored == 2
    assert abs(k.hr_savings - 0.2) < 1e-9
    assert k.hr_spot_n == 0
    assert k.hr_calibrated is None  # no count_tokens samples → raw only


def spot_turn(i, tik_before, tik_after, cl_before, cl_after):
    lens = {
        "tokens_before": tik_before,
        "tokens_after": tik_after,
        "spot_check": {"claude_tokens_before": cl_before, "claude_tokens_after": cl_after},
    }
    return turn_from_recent(recent_row(id=i, metrics={**METRICS, "headroom": lens}))


def test_aggregate_headroom_calibration():
    # 3 spot-checked turns: tiktoken sees 10% savings, count_tokens sees 20%
    # → factor 2 applied to the population figure. Multiplicative: raw × 2.
    spots = [spot_turn(i, 100_000, 90_000, 80_000, 64_000) for i in (1, 2, 3)]
    k = aggregate(spots)
    assert k.hr_spot_n == 3
    assert abs(k.hr_savings - 0.1) < 1e-9
    assert abs(k.hr_factor - 2.0) < 1e-9
    assert abs(k.hr_calibrated - 0.2) < 1e-9

    # Below MIN_SPOT_SAMPLES the raw figure stands alone.
    k2 = aggregate(spots[:2])
    assert k2.hr_spot_n == 2
    assert k2.hr_calibrated is None

    # Nothing fired (tiktoken savings 0) → no factor, never invents savings.
    flat = [spot_turn(i, 100_000, 100_000, 80_000, 80_000) for i in (1, 2, 3)]
    assert aggregate(flat).hr_calibrated is None

    # Calibrated value is capped at 100% (big factor × high raw savings).
    wild = [spot_turn(i, 100_000, 90_000, 10_000, 100) for i in (1, 2, 3)]
    big_raw = turn_from_recent(recent_row(
        id=4, metrics={**METRICS, "headroom": {"tokens_before": 100_000, "tokens_after": 10_000}}
    ))
    assert aggregate([*wild, big_raw]).hr_calibrated == 1.0


def shadow_turn(i, *, policy_cost, actual_in, spot=None, warmup=None, reset=False):
    """A turn carrying a priced shadow ledger; actual input cost is split
    across the three input-side components to prove they're all summed."""
    cost_usd = {
        "total": actual_in + 0.01,  # +output; must NOT enter the shadow math
        "no_cache_total": 1.0,
        "cache_savings": 0.5,
        "uncached_input": actual_in * 0.5,
        "cache_writes": actual_in * 0.3,
        "cache_reads": actual_in * 0.2,
        "output": 0.01,
    }
    lens = {
        "tokens_before": 100_000,
        "tokens_after": 100_000,
        "shadow": {
            "policy": {"prefix_tokens": 1, "new_tokens": 1, "total_tokens": 2, "cost_usd": policy_cost},
            "marginal": {"prefix_tokens": 1, "new_tokens": 1, "total_tokens": 2},
            "fired": False,
            "reset": reset,
        },
    }
    if warmup is not None:
        lens["shadow"]["warmup"] = warmup
    if spot:
        lens["spot_check"] = spot
    metrics = {**METRICS, "cache": {"cache_hit_ratio": 0.9, "cost_usd": cost_usd}, "headroom": lens}
    return turn_from_recent(recent_row(id=i, metrics=metrics))


def test_aggregate_shadow_ledger_sums_matching_turns():
    a = shadow_turn(1, policy_cost=0.10, actual_in=0.30)
    b = shadow_turn(2, policy_cost=0.50, actual_in=0.20)
    # Scored by the lens but no shadow block: excluded from BOTH shadow sums.
    plain = turn_from_recent(recent_row(
        id=3, metrics={**METRICS, "headroom": {"tokens_before": 10, "tokens_after": 10}}
    ))
    k = aggregate([a, b, plain])
    assert k.shadow_scored == 2
    assert abs(k.shadow_cost - 0.60) < 1e-9
    assert abs(k.shadow_actual - 0.50) < 1e-9
    assert abs(k.shadow_net - (-0.10)) < 1e-9  # negative: compression costs money
    assert k.shadow_factor is None  # no count_tokens samples yet
    assert k.shadow_net_calibrated is None


def test_aggregate_shadow_skips_warmup_turns():
    a = shadow_turn(1, policy_cost=0.10, actual_in=0.30)
    # Warmup (reset + the turn after): instrument boundary, not economics.
    boundary = shadow_turn(2, policy_cost=8.00, actual_in=0.40, warmup=True)
    # Legacy row without the warmup field falls back to its reset flag.
    legacy_reset = shadow_turn(3, policy_cost=8.00, actual_in=0.40, reset=True)
    k = aggregate([a, boundary, legacy_reset])
    assert k.shadow_scored == 1
    assert abs(k.shadow_cost - 0.10) < 1e-9
    assert abs(k.shadow_actual - 0.30) < 1e-9
    # A legacy non-reset row (no warmup field) still counts.
    legacy_ok = shadow_turn(4, policy_cost=0.20, actual_in=0.10)
    assert aggregate([a, legacy_ok]).shadow_scored == 2


def test_aggregate_shadow_calibration_scales_shadow_cost():
    # 3 spot-checked shadow turns: Claude counts 80k where tiktoken counts
    # 100k → factor 0.8 shrinks the tiktoken-denominated shadow side.
    spot = {"claude_tokens_before": 80_000, "claude_tokens_after": 80_000}
    turns = [shadow_turn(i, policy_cost=0.10, actual_in=0.10, spot=spot) for i in (1, 2, 3)]
    k = aggregate(turns)
    assert abs(k.shadow_factor - 0.8) < 1e-9
    assert abs(k.shadow_net - 0.0) < 1e-9  # raw: dead even
    assert abs(k.shadow_net_calibrated - (0.30 - 0.30 * 0.8)) < 1e-9  # calibrated: positive
    # Below MIN_SPOT_SAMPLES the raw net stands alone.
    assert aggregate(turns[:2]).shadow_net_calibrated is None


def test_turn_store_dedupes_and_orders():
    store = TurnStore()
    live = turn_from_ws(ws_event(id=5))
    store.add_live(live)
    store.add_live(turn_from_ws(ws_event(id=5, status=500)))  # replaces id 5
    store.add_backfill([turn_from_recent(recent_row(id=3)), turn_from_recent(recent_row(id=5))])
    assert [t.id for t in store.turns] == [3, 5]
    assert store.turns[1].status == 200  # backfill row won for id 5


def test_turn_store_backfill_keeps_newer_live_turns():
    store = TurnStore()
    store.add_live(turn_from_ws(ws_event(id=10)))
    store.add_backfill([turn_from_recent(recent_row(id=1))])
    assert [t.id for t in store.turns] == [1, 10]


def test_turn_store_caps_at_1000():
    store = TurnStore()
    store.add_backfill([turn_from_recent(recent_row(id=i)) for i in range(1, 1200)])
    assert len(store.turns) == 1000
    assert store.turns[0].id == 200
    store.add_live(turn_from_ws(ws_event(id=1300)))
    assert len(store.turns) == 1000
    assert store.turns[-1].id == 1300


def test_composition_series_groups_and_remainder():
    full = {
        **METRICS,
        "composition": {
            "total_bytes": 100,
            "bytes": {
                "system": 10, "tool_definitions": 5,       # → prompt
                "user_text": 20,                            # → user
                "assistant_text": 15, "thinking": 5, "tool_use": 5,  # → assistant
                "tool_results": 30,                         # → tool results
                "images": 10,                               # → other (remainder)
            },
        },
    }
    turns = [
        turn_from_recent(recent_row(id=1, metrics=full)),
        turn_from_recent(recent_row(id=2, metrics=None)),  # skipped
        turn_from_recent(recent_row(id=3)),  # METRICS has no bytes dict → all "other"
    ]
    assert composition_series(turns) == [
        {"prompt": 15, "user": 20, "assistant": 25, "tool results": 30, "other": 10},
        {"prompt": 0, "user": 0, "assistant": 0, "tool results": 0, "other": 40_000},
    ]


def test_stack_heights_zero_baseline():
    # Whole bar at max: fills all cells; half-total bar fills half.
    assert stack_heights([50, 50], 100, 14) == [7, 7]
    assert stack_heights([25, 25], 100, 14) == [4, 3]  # cumulative rounding, sum exact
    # Cumulative rounding never drifts: 14 equal segments land 1 cell each.
    assert stack_heights([1] * 14, 14, 14) == [1] * 14
    # A nonzero bar never vanishes: its largest segment gets the 1-cell floor.
    assert stack_heights([1, 3, 1], 1000, 14) == [0, 1, 0]
    assert stack_heights([0, 0], 100, 14) == [0, 0]
    assert stack_heights([10], 0, 14) == [0]


def test_latest_delta_skips_first_turns():
    first = {**METRICS, "delta": {"first_turn": True}}
    turns = [
        turn_from_recent(recent_row(id=1)),
        turn_from_recent(recent_row(id=2, metrics=first)),
    ]
    assert latest_delta(turns) == METRICS["delta"]
    assert latest_delta([turn_from_recent(recent_row(id=2, metrics=first))]) is None


def test_session_helpers():
    turns = [
        turn_from_recent(recent_row(id=1, session_id="aaaa")),
        turn_from_recent(recent_row(id=2, session_id="bbbb")),
        turn_from_recent(recent_row(id=3, session_id="bbbb")),
        turn_from_recent(recent_row(id=4, session_id=None)),
    ]
    assert sessions_by_count(turns) == [("bbbb", 2), ("aaaa", 1), ("unknown", 1)]
    assert [t.id for t in filter_session(turns, "bbbb")] == [2, 3]
    assert [t.id for t in filter_session(turns, "unknown")] == [4]
    assert filter_session(turns, None) == turns
    # The live chart follows the newest turn's session, not the busiest one.
    assert latest_session(turns) == "unknown"
    assert latest_session(turns[:3]) == "bbbb"
    assert latest_session([]) is None


def test_formatters():
    from contextlab.tui.model import fmt_usd_signed

    assert fmt_usd(2.5) == "$2.50"
    assert fmt_usd(0.05) == "$0.050"
    assert fmt_usd(0.0012) == "$0.0012"
    assert fmt_usd_signed(0.05) == "+$0.050"
    assert fmt_usd_signed(-0.05) == "-$0.050"
    assert fmt_usd_signed(0.0) == "+$0.0000"
    assert short_model("claude-opus-4-8-20260101") == "opus-4-8"
    assert short_model(None) == "?"
    assert short_session("abcdef12-3456") == "abcdef12"
    assert short_session(None) == "unknown"


def test_short_dir_and_session_fallback():
    from contextlab.tui.model import short_dir, workdirs_by_session

    assert short_dir("/home/u/projects/api") == "api"
    assert short_dir("/home/u/projects/billing_dashboard") == "billing_d…"
    assert short_dir("/home/u/projects/billing_dashboard", cap=24) == "billing_dashboard"
    assert short_dir(None) == "—"

    with_dir = turn_from_recent(
        recent_row(id=1, metrics={**METRICS, "workdir": "/home/u/projects/app"})
    )
    housekeeping = turn_from_recent(recent_row(id=2))  # METRICS has no workdir
    assert workdirs_by_session([with_dir, housekeeping]) == {
        "abcdef12-3456": "/home/u/projects/app"
    }

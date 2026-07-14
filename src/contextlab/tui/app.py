"""`contextlab top` — the web dashboard's vital signs in a two-thirds-height tmux pane.

Layout (~100x25): one-line KPI bar, live request feed (2/3 of the flexible
space), stacked context-composition chart (the remaining 1/3).
Keys: q quit · p suspend into a python REPL (workdir's venv) · s cycle session.
"""

from __future__ import annotations

import os
import subprocess

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import DataTable, Footer, Static

from .client import ConnState, EventClient
from .model import (
    TurnStore,
    aggregate,
    composition_series,
    filter_session,
    fmt_bytes,
    fmt_compact,
    fmt_pct,
    fmt_time,
    fmt_usd,
    fmt_usd_signed,
    latest_delta,
    latest_session,
    sessions_by_count,
    short_dir,
    short_model,
    short_session,
    workdirs_by_session,
)
from .spark import GROUP_COLORS, GROUP_ORDER, StackedSparkline

FEED_ROWS = 100
FEED_COLUMNS = ("time", "model", "dir", "session", "in", "out", "cache", "hit%", "cost", "ttft", "hr$")
DIR_CAP_MIN, DIR_CAP_MAX = 10, 24  # dir column: floor for ~86-col panes, ceiling on wide ones
CELL_PAD_MAX = 3

STATE_DOT = {
    ConnState.CONNECTING: "[yellow]●[/] connecting",
    ConnState.LIVE: "[green]●[/] live",
    ConnState.RETRYING: "[red]●[/] retrying",
}


class ContextTop(App):
    TITLE = "contextlab top"

    CSS = """
    #kpis { height: 1; padding: 0 1; background: $surface; }
    #feed { height: 2fr; scrollbar-size-vertical: 1; }
    #spark-title { height: 1; padding: 0 1; color: $text-muted; }
    #legend { height: 1; padding: 0 1; color: $text-muted; }
    /* The y-axis sits on the right, next to the newest bar — which also keeps
       that bar clear of the feed's scrollbar column above. */
    #spark-row { height: 1fr; min-height: 1; margin: 0 1 0 1; }
    #yaxis { width: 10; height: 100%; padding: 0 0 0 1; color: $text-muted; text-align: left; }
    #spark { width: 1fr; height: 100%; }
    #growth { height: 1; padding: 0 1; color: $text-muted; }
    """

    BINDINGS = [
        ("q", "quit", "quit"),
        ("p", "python_repl", "python"),
        ("s", "cycle_session", "session"),
    ]

    def __init__(self, url: str, workdir: str, limit: int = 500) -> None:
        super().__init__()
        self._url = url
        self._workdir = workdir
        self._limit = limit
        self._store = TurnStore()
        self._state = ConnState.CONNECTING
        self._session: str | None = None  # None = all sessions

    def compose(self) -> ComposeResult:
        yield Static(id="kpis")
        yield DataTable(id="feed", cursor_type="row", zebra_stripes=True)
        yield Static(id="spark-title")
        yield Static(id="legend")
        with Horizontal(id="spark-row"):
            yield StackedSparkline(id="spark")
            yield Static(id="yaxis")
        yield Static(id="growth")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#feed", DataTable)
        table.add_columns(*FEED_COLUMNS)
        self.query_one("#legend", Static).update(
            "  ".join(f"[{GROUP_COLORS[g]}]■[/] {g}" for g in GROUP_ORDER)
        )
        client = EventClient(
            self._url,
            on_backfill=self._on_backfill,
            on_turn=self._on_turn,
            on_state=self._on_state,
            limit=self._limit,
        )
        self.run_worker(client.run(), exclusive=True)
        self._render_all()

    # --- event-client callbacks (run on the app's own loop) ---

    def _on_backfill(self, turns) -> None:
        self._store.add_backfill(turns)
        self._render_all()

    def _on_turn(self, turn) -> None:
        self._store.add_live(turn)
        self._render_all()

    def _on_state(self, state: ConnState) -> None:
        self._state = state
        self._render_kpis()

    def on_resize(self) -> None:
        # The y-axis pins its min label to the bottom row, the feed sizes its
        # dir column and padding to the pane width, and the KPI bar drops its
        # dim headroom derivation when narrow — all must track resizes.
        # Deferred: when this app-level handler fires, children still carry
        # their pre-resize sizes (even call_after_refresh lands too early),
        # so measuring immediately renders one resize behind.
        self.set_timer(0.05, self._render_all)

    # --- rendering ---

    def _filtered(self):
        return filter_session(self._store.turns, self._session)

    def _render_all(self) -> None:
        self._render_kpis()
        self._render_feed()
        self._render_growth()

    def _render_kpis(self) -> None:
        turns = self._filtered()
        k = aggregate(turns)
        scope = short_session(self._session) if self._session else "all"
        if self._session is None and (n := len(sessions_by_count(turns))):
            scope = f"all/{n}"
        # Shadow lens: what compress() would have saved, — until a turn is
        # scored. Once count_tokens calibration kicks in, show the derivation
        # left-to-right (dim raw ×factor →, bright result) so the headline
        # number is unambiguously the calibrated one.
        headroom = "—"
        if k.hr_scored:
            if k.hr_calibrated is not None:
                headroom = (
                    f"[dim]{fmt_pct(k.hr_savings)} (×{k.hr_factor:.2f} error_factor) →[/] "
                    f"{fmt_pct(k.hr_calibrated)}"
                )
            else:
                headroom = fmt_pct(k.hr_savings)
        # No cache-savings figure here: next to "headroom" a bare "saved $X"
        # reads as Headroom's doing when it's the KV cache's. The web tiles
        # have room to attribute it; this bar doesn't.
        # "shadow" is different — it IS attributable: the cache-adjusted
        # input-dollar net if compression had been on all session. Negative
        # (red) means the cache it busts costs more than the tokens it saves.
        shadow = "—"
        if k.shadow_scored:
            net = k.shadow_net_calibrated if k.shadow_net_calibrated is not None else k.shadow_net
            shadow = f"[{'green' if net >= 0 else 'red'}]{fmt_usd_signed(net)}[/]"

        # Every KPI in this bar is a window over the turn buffer, not
        # all-time — the spend label carries the window size so a dollar
        # figure is never mistaken for cumulative spend (the old standalone
        # "turns" entry was the same number, unlabeled as a window).
        def bar(hr: str) -> str:
            return (
                f"[b]spend ({k.turns}t)[/] [green]{fmt_usd(k.spend)}[/]  "
                f"[b]cache hit[/] {fmt_pct(k.hit_ratio)}  "
                f"[b]headroom[/] {hr}  [b]shadow[/] {shadow}  "
                f"[b]scope[/] {scope}  {STATE_DOT[self._state]}"
            )

        kpis = self.query_one("#kpis", Static)
        text = bar(headroom)
        # Narrow panes drop the dim derivation, keeping just the calibrated
        # value — the full audit trail stays on the web tile.
        width = kpis.size.width - 2  # padding: 0 1
        if (
            k.hr_calibrated is not None
            and width > 0
            and Text.from_markup(text).cell_len > width
        ):
            text = bar(fmt_pct(k.hr_calibrated))
        kpis.update(text)

    def _render_feed(self) -> None:
        table = self.query_one("#feed", DataTable)
        table.clear()
        # Built from all turns, not the filtered view, so a session-scoped
        # feed still resolves directories learned from other turns.
        dirs = workdirs_by_session(self._store.turns)
        rows = []
        for t in reversed(self._filtered()[-FEED_ROWS:]):
            total_in = t.cache_read + t.cache_creation + t.input_tokens
            cost = (t.metrics or {}).get("cache", {}).get("cost_usd") if t.metrics else None
            wd = (t.metrics or {}).get("workdir") if t.metrics else None
            lens = (t.metrics or {}).get("headroom") if t.metrics else None
            shadow = (lens or {}).get("shadow") or {}
            policy = shadow.get("policy") or {}
            hr_net = None
            # Warmup turns show — rather than a boundary-artifact number.
            if "cost_usd" in policy and cost and not shadow.get("warmup", shadow.get("reset")):
                actual_in = cost["uncached_input"] + cost["cache_writes"] + cost["cache_reads"]
                hr_net = actual_in - policy["cost_usd"]
            rows.append((
                fmt_time(t.ts),
                short_model(t.model),
                wd or dirs.get(t.session_id or "unknown"),  # full path; capped below
                short_session(t.session_id),
                fmt_compact(total_in),
                fmt_compact(t.output_tokens),
                fmt_compact(t.cache_read),
                fmt_pct(t.cache_read / total_in) if total_in else "—",
                fmt_usd(cost["total"]) if cost else "—",
                f"{t.ttft_s:.1f}s" if t.ttft_s is not None else "—",
                fmt_usd_signed(hr_net) if hr_net is not None else "—",
            ))
        dir_cap, pad = self._fit_feed(rows)
        table.cell_padding = pad
        di = FEED_COLUMNS.index("dir")
        for r in rows:
            table.add_row(*r[:di], short_dir(r[di], cap=dir_cap), *r[di + 1 :])

    def _fit_feed(self, rows) -> tuple[int, int]:
        """Size the feed to the pane: baseline is the ~86-column layout
        (dir capped at 10, padding 1); wider panes spend the slack on a longer
        dir column first, then on inter-column padding, so the table spans the
        pane instead of leaving a blank right gutter."""
        dir_cap, pad = DIR_CAP_MIN, 1
        avail = self.query_one("#feed", DataTable).scrollable_content_region.width
        if not avail or not rows:
            return dir_cap, pad
        di = FEED_COLUMNS.index("dir")
        widths = [
            max(len(FEED_COLUMNS[i]), *(len(r[i]) for r in rows))
            for i in range(len(FEED_COLUMNS))
            if i != di
        ]
        longest_dir = max(len(short_dir(r[di], cap=999)) for r in rows)
        slack = avail - (sum(widths) + min(longest_dir, dir_cap) + 2 * pad * len(FEED_COLUMNS))
        if slack > 0:
            grow = min(slack, max(0, min(longest_dir, DIR_CAP_MAX) - dir_cap))
            dir_cap += grow
            pad += min((slack - grow) // (2 * len(FEED_COLUMNS)), CELL_PAD_MAX - 1)
        return dir_cap, pad

    def _render_growth(self) -> None:
        turns = self._filtered()
        # The sparkline only makes sense within one conversation; when scoped
        # to "all", chart the most recently active session rather than
        # interleaved contexts (or a finished session that merely has more
        # stored turns — that hid the live session's compaction cliff).
        scope_note = ""
        if self._session is None and (live := latest_session(turns)):
            turns = filter_session(turns, live)
            scope_note = f" — latest session ({short_session(live)})"
        spark = self.query_one("#spark", StackedSparkline)
        # One column per turn: trim to the chart's width (80 before first layout).
        series = composition_series(turns)[-(spark.size.width or 80) :]
        title = self.query_one("#spark-title", Static)
        axis = self.query_one("#yaxis", Static)
        if not series:
            title.update("[b]context growth[/] — no data yet")
            axis.update("")
            spark.update_data([])
            self.query_one("#growth", Static).update("[dim]context: no data yet[/]")
            return
        spark.update_data(series)
        totals = [sum(row.values()) for row in series]
        title.update(
            f"[b]context growth[/] — composition per request, last {len(series)} turns{scope_note}"
        )
        # Zero-baselined (stacked segments must keep honest proportions).
        height = self.query_one("#spark-row").size.height
        labels = [fmt_bytes(max(totals))]
        if height > 1:
            labels += [""] * (height - 2) + ["0 B"]
        axis.update("\n".join(labels))
        delta = latest_delta(turns)
        line = f"context {fmt_bytes(totals[-1])}"
        if delta:
            line += (
                f"   Δ [green]+{fmt_bytes(delta.get('new_bytes') or 0)}[/] new"
                f" / {fmt_bytes(delta.get('resent_bytes') or 0)} resent"
                f" ({fmt_pct(delta.get('resent_fraction') or 0)})"
            )
        self.query_one("#growth", Static).update(line)

    # --- actions ---

    def action_cycle_session(self) -> None:
        order: list[str | None] = [None, *(s for s, _ in sessions_by_count(self._store.turns))]
        i = order.index(self._session) if self._session in order else 0
        self._session = order[(i + 1) % len(order)]
        self._render_all()

    def action_python_repl(self) -> None:
        venv_python = os.path.join(self._workdir, ".venv", "bin", "python")
        python = venv_python if os.path.exists(venv_python) else "python3"
        with self.suspend():
            print(f"contextlab top suspended — {python} (Ctrl-D returns to the dashboard)")
            subprocess.run([python], cwd=self._workdir)


def run(url: str, workdir: str, limit: int = 500) -> None:
    ContextTop(url, workdir, limit).run()

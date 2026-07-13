"""Stacked composition sparkline — the terminal port of the web dashboard's
per-turn composition bars (TurnStream.tsx), rotated into a time series.

One character column per request, oldest→newest, right-aligned. Each column
stacks its COMP_GROUPS bytes bottom-up at half-cell resolution (▀▄█ with
fg/bg colors doubles the vertical resolution of the row grid). Zero-baselined:
stacked segments must keep honest proportions, unlike textual's min→max
Sparkline this replaces.
"""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

from .model import COMP_GROUPS, COMP_OTHER, stack_heights

# One color per group, lifted from dashboard/src/index.css dark-theme --cat-*
# values (each group shows its dominant category's color) so both dashboards
# speak the same color language.
GROUP_COLORS = {
    "prompt": "#3987e5",  # --cat-system
    "user": "#c98500",  # --cat-user
    "assistant": "#008300",  # --cat-assistant
    "tool results": "#e66767",  # --cat-toolresults
    COMP_OTHER: "#898781",  # --cat-other
}
GROUP_ORDER = [name for name, _ in COMP_GROUPS] + [COMP_OTHER]


class StackedSparkline(Static):
    """Zero-baselined stacked bar chart; scale is 0 → max visible total."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._series: list[dict[str, int]] = []

    def update_data(self, series: list[dict[str, int]]) -> None:
        self._series = series
        self._redraw()

    def on_resize(self) -> None:
        self._redraw()

    def _redraw(self) -> None:
        width, height = self.size.width, self.size.height
        if not width or not height or not self._series:
            self.update("")
            return
        series = self._series[-width:]
        max_total = max(sum(row.values()) for row in series)
        half = height * 2
        # Per column: color of each half-cell, bottom-up (None = empty).
        columns: list[list[str | None]] = []
        for row in series:
            values = [row.get(g) or 0 for g in GROUP_ORDER]
            cells: list[str | None] = []
            for group, h in zip(GROUP_ORDER, stack_heights(values, max_total, half)):
                cells.extend([GROUP_COLORS[group]] * h)
            cells.extend([None] * (half - len(cells)))
            columns.append(cells)
        pad = " " * (width - len(columns))
        lines = []
        for r in range(height - 1, -1, -1):  # r = row from bottom; top first
            text = Text(pad)
            for cells in columns:
                lower, upper = cells[2 * r], cells[2 * r + 1]
                if lower is None and upper is None:
                    text.append(" ")
                elif lower == upper:
                    text.append("█", style=lower)
                elif upper is None:
                    text.append("▄", style=lower)
                elif lower is None:
                    text.append("▀", style=upper)
                else:
                    # ▄ paints the lower half fg over a bg-filled cell.
                    text.append("▄", style=f"{lower} on {upper}")
            lines.append(text)
        self.update(Text("\n").join(lines))

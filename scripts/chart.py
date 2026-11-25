"""An error-bar chart as inline SVG, with the standard library only.

Each group is a few rows, one per series: a dot at the score and a line across its 95% interval.
The marks take their colours from CSS custom properties the site's light and dark stylesheets set
(`--chart-<key>`), so the chart follows the page's colour mode; text and gridlines use the page's own
text colour. Each series also has its own marker shape, so colour is never the only cue, and each
mark carries a <title> that browsers show on hover. The same numbers are in a table beside it.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from html import escape

WIDTH = 360
LEFT, RIGHT = 8, 16
TOP = 44  # room for the legend
GROUP_LABEL = 22
ROW = 22
GROUP_GAP = 14
AXIS = 46
RADIUS = 5


@dataclass(frozen=True)
class Series:
    name: str
    key: str  # the CSS custom property is --chart-<key>
    shape: str  # "circle" or "square"


def _ticks(low: float, high: float) -> tuple[float, float, list[float]]:
    step = 10 if high - low <= 60 else 20
    start = math.floor(low / step) * step
    end = math.ceil(high / step) * step
    if end == start:
        end = start + step
    count = round((end - start) / step)
    return start, end, [start + i * step for i in range(count + 1)]


def _marker(shape: str, x: float, y: float, key: str) -> str:
    style = f'class="chart-mark" style="fill:var(--chart-{key})"'
    if shape == "square":
        s = RADIUS * 1.8
        return f'<rect x="{x - s / 2:.1f}" y="{y - s / 2:.1f}" width="{s:.1f}" height="{s:.1f}" rx="1.5" {style}/>'
    return f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{RADIUS}" {style}/>'


def error_bars(
    *,
    title: str,
    description: str,
    groups: Sequence[str],
    series: Sequence[Series],
    values: Mapping[tuple[str, str], tuple[float, float, float]],
    axis_label: str,
    chart_id: str = "chart",
) -> str:
    """values maps (group, series name) to (estimate, low, high)."""
    for key, (mid, low, high) in values.items():
        if not all(map(math.isfinite, (mid, low, high))) or not low <= mid <= high:
            raise ValueError(f"{key}: {mid} is not inside [{low}, {high}]")
    start, end, ticks = _ticks(min(v[1] for v in values.values()), max(v[2] for v in values.values()))
    plot_width = WIDTH - LEFT - RIGHT

    def x(value: float) -> float:
        return LEFT + (value - start) / (end - start) * plot_width

    block = GROUP_LABEL + ROW * len(series) + GROUP_GAP
    plot_bottom = TOP + block * len(groups)
    height = plot_bottom + AXIS

    out = [
        f'<svg class="chart" role="img" viewBox="0 0 {WIDTH} {height}" aria-labelledby="{chart_id}-title {chart_id}-desc"'
        ' xmlns="http://www.w3.org/2000/svg" font-family="inherit">',
        f'<title id="{chart_id}-title">{escape(title)}</title>',
        f'<desc id="{chart_id}-desc">{escape(description)}</desc>',
    ]
    # The legend: a marker and the series' name, in the page's text colour.
    legend_x = LEFT
    for s in series:
        out.append(_marker(s.shape, legend_x + RADIUS, 16, s.key))
        out.append(f'<text x="{legend_x + 2 * RADIUS + 6:.1f}" y="21" font-size="14" fill="currentColor">{escape(s.name)}</text>')
        legend_x += 2 * RADIUS + 14 + 7.2 * len(s.name)
    # Gridlines and their values.
    for tick in ticks:
        out.append(
            f'<line x1="{x(tick):.1f}" y1="{TOP - 4}" x2="{x(tick):.1f}" y2="{plot_bottom}" stroke="currentColor" stroke-opacity="0.15" stroke-width="1"/>'
        )
        out.append(
            f'<text x="{x(tick):.1f}" y="{plot_bottom + 18}" font-size="13" text-anchor="middle" fill="currentColor">{tick:g}</text>'
        )
    out.append(
        f'<text x="{LEFT + plot_width / 2:.1f}" y="{plot_bottom + 38}" font-size="13" text-anchor="middle" fill="currentColor">{escape(axis_label)}</text>'
    )
    # The marks.
    for g, group in enumerate(groups):
        top = TOP + g * block
        out.append(f'<text x="{LEFT}" y="{top + 15}" font-size="14" font-weight="600" fill="currentColor">{escape(group)}</text>')
        for r, s in enumerate(series):
            mid, low, high = values[(group, s.name)]
            y = top + GROUP_LABEL + ROW * r + ROW / 2
            out.append("<g>")
            out.append(
                f"<title>{escape(s.name)}, {escape(group)}: {mid:.1f} (95% interval {low:.1f} to {high:.1f})</title>"
            )
            out.append(
                f'<line x1="{x(low):.1f}" y1="{y:.1f}" x2="{x(high):.1f}" y2="{y:.1f}" stroke-width="2" stroke-linecap="round" style="stroke:var(--chart-{s.key})"/>'
            )
            out.append(_marker(s.shape, x(mid), y, s.key))
            out.append("</g>")
    out.append("</svg>")
    return "\n".join(out)

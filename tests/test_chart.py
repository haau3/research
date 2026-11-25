"""scripts/chart.py draws an error-bar chart a screen reader can describe and a colour-blind reader can
tell apart.

    python3 -I -m unittest discover -s tests -p test_chart.py
"""

from __future__ import annotations

import re
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from chart import Series, error_bars  # noqa: E402

SVG = "{http://www.w3.org/2000/svg}"


def draw(**overrides) -> str:
    arguments = dict(
        title="Scores by model",
        description="Model A 60 (55 to 65); Model B 40 (35 to 45).",
        groups=["All", "Some"],
        series=[Series("Model A", "a", "circle"), Series("Model B", "b", "square")],
        values={
            ("All", "Model A"): (60, 55, 65),
            ("All", "Model B"): (40, 35, 45),
            ("Some", "Model A"): (62, 50, 74),
            ("Some", "Model B"): (38, 30, 46),
        },
        axis_label="Score out of 100",
    )
    return error_bars(**(arguments | overrides))


class ErrorBars(unittest.TestCase):
    def test_it_is_an_image_with_its_title_and_description(self) -> None:
        svg = ET.fromstring(draw())
        self.assertEqual(svg.get("role"), "img")
        self.assertEqual(svg.get("aria-labelledby"), "chart-title chart-desc")
        self.assertEqual(svg.find(f"{SVG}title").text, "Scores by model")
        self.assertIn("Model A 60", svg.find(f"{SVG}desc").text)

    def test_each_series_has_its_own_shape_colour_and_legend(self) -> None:
        text = draw()
        self.assertEqual(text.count("var(--chart-a)"), 1 + 2 * 2)  # legend, then a line and a dot per group
        self.assertEqual(text.count("var(--chart-b)"), 1 + 2 * 2)
        self.assertEqual(len(re.findall(r"<circle", text)), 1 + 2)
        self.assertEqual(len(re.findall(r"<rect", text)), 1 + 2)
        self.assertIn(">Model A</text>", text)
        self.assertIn(">Model B</text>", text)

    def test_each_mark_says_what_it_shows_on_hover(self) -> None:
        self.assertIn("<title>Model B, Some: 38.0 (95% interval 30.0 to 46.0)</title>", draw())

    def test_the_axis_covers_every_interval_in_round_steps(self) -> None:
        ticks = re.findall(r'text-anchor="middle" fill="currentColor">(-?\d+)</text>', draw())
        self.assertEqual(ticks, ["30", "40", "50", "60", "70", "80"])

    def test_text_is_never_in_a_series_colour(self) -> None:
        for element in ET.fromstring(draw()).iter(f"{SVG}text"):
            self.assertEqual(element.get("fill"), "currentColor")

    def test_an_estimate_outside_its_interval_is_refused(self) -> None:
        values = {("All", "Model A"): (70, 55, 65), ("All", "Model B"): (40, 35, 45)}
        with self.assertRaises(ValueError):
            draw(groups=["All"], values=values)

    def test_names_are_escaped(self) -> None:
        svg = draw(title="A <b> & C")
        self.assertIn("A &lt;b&gt; &amp; C", svg)
        ET.fromstring(svg)


if __name__ == "__main__":
    unittest.main()

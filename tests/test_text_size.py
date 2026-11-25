"""scripts/check_text_size.py passes body text of 16 px or more and fails anything smaller, with a
planted stylesheet as each case's control."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from check_text_size import body_px, main  # noqa: E402

QUARTO = ":root{--bs-root-font-size: 17px;--bs-body-font-size:1rem}"


def site(*sheets: str) -> tempfile.TemporaryDirectory:
    folder = tempfile.TemporaryDirectory()
    styles = Path(folder.name) / "site_libs" / "bootstrap"
    styles.mkdir(parents=True)
    for n, css in enumerate(sheets):
        (styles / f"bootstrap-{n}.min.css").write_text(css)
    (styles / "bootstrap-icons.css").write_text("--bs-body-font-size:0.5rem")
    return folder


class BodyPx(unittest.TestCase):
    def test_quartos_root_and_body(self) -> None:
        self.assertEqual(body_px(QUARTO), 17.0)

    def test_a_smaller_root(self) -> None:
        self.assertEqual(body_px(":root{--bs-root-font-size: 15px;--bs-body-font-size:1rem}"), 15.0)

    def test_a_smaller_body(self) -> None:
        self.assertAlmostEqual(body_px(":root{--bs-root-font-size: 17px;--bs-body-font-size:0.875rem}"), 14.875)

    def test_a_body_in_px_and_no_root(self) -> None:
        self.assertEqual(body_px(":root{--bs-body-font-size:15px}"), 15.0)
        self.assertEqual(body_px(":root{--bs-body-font-size:1rem}"), 16.0)

    def test_a_stylesheet_that_does_not_say(self) -> None:
        self.assertIsNone(body_px("body{color:red}"))


class Main(unittest.TestCase):
    def test_both_modes_at_17_px_pass(self) -> None:
        with site(QUARTO, QUARTO) as folder:
            self.assertEqual(main([folder]), 0)

    def test_one_mode_at_15_px_fails(self) -> None:
        # The control: the same site with the dark stylesheet planted at 15 px.
        with site(QUARTO, ":root{--bs-root-font-size: 15px;--bs-body-font-size:1rem}") as folder:
            self.assertEqual(main([folder]), 1)

    def test_no_stylesheet_or_no_size_cannot_check(self) -> None:
        with site() as folder:
            self.assertEqual(main([folder]), 2)
        with site("body{color:red}") as folder:
            self.assertEqual(main([folder]), 2)


if __name__ == "__main__":
    unittest.main()

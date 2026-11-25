"""Fails when the site's body text would be smaller than 16 px, in either colour mode.

    python3 -I scripts/check_text_size.py _site

It reads the rendered stylesheets (one per colour mode): the root size Quarto sets and the body size
in it. It uses only the standard library. Exit 0 when every stylesheet's body text is 16 px or
more, 1 when one is smaller, 2 when a stylesheet cannot be read or there is none.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

MIN_PX = 16.0
BROWSER_ROOT_PX = 16.0
ROOT = re.compile(r"--bs-root-font-size:\s*([\d.]+)px")
BODY = re.compile(r"--bs-body-font-size:\s*([\d.]+)(rem|px)")


def body_px(css: str) -> float | None:
    """The body text's size in px, or None when the stylesheet does not say."""
    body = BODY.search(css)
    if not body:
        return None
    root = ROOT.search(css)
    root_px = float(root.group(1)) if root else BROWSER_ROOT_PX
    size, unit = float(body.group(1)), body.group(2)
    return size * root_px if unit == "rem" else size


def stylesheets(site: Path) -> list[Path]:
    # Quarto writes bootstrap-<hash>.min.css and bootstrap-dark-<hash>.min.css; the icons are not text.
    return sorted(p for p in (site / "site_libs" / "bootstrap").glob("bootstrap*.css") if "icons" not in p.name)


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: check_text_size.py <site directory>", file=sys.stderr)
        return 2
    sheets = stylesheets(Path(argv[0]))
    if not sheets:
        print(f"cannot check: no stylesheet under {argv[0]}", file=sys.stderr)
        return 2
    status = 0
    for sheet in sheets:
        px = body_px(sheet.read_text(encoding="utf-8"))
        if px is None:
            print(f"cannot check: {sheet.name} gives no body text size", file=sys.stderr)
            return 2
        ok = px >= MIN_PX
        print(f"{sheet.name}: body text {px:g} px{'' if ok else f', under {MIN_PX:g} px'}", file=sys.stderr)
        status = status or (0 if ok else 1)
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

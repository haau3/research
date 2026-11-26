"""Every note can be found: it turns on Google Scholar's citation tags and gives the citation they are
filled from, with its own address; and the site gives every page link previews and a canonical address.
Google Scholar's tags stay off the other pages, so they never look like papers.

    uv run python -m unittest discover -s tests -p test_note_search.py
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = "https://research.haau3.com"


def front_matter(text: str) -> str:
    if not text.startswith("---\n"):
        return ""
    return text[4 : text.index("\n---", 4)]


def search_problems(folder: str, text: str) -> list[str]:
    """What a note's front matter is missing for search, by name."""
    head = front_matter(text).splitlines()
    wanted = {
        "google-scholar: true": "google-scholar: true",
        "appendix-cite-as: false": "appendix-cite-as: false",
        "citation:": "a citation block",
        "  type: report": "citation type report",
        "  publisher: haau3 Research": "citation publisher",
        f"  url: {SITE}/notes/{folder}/": f"citation url {SITE}/notes/{folder}/",
    }
    return [name for line, name in wanted.items() if line not in head]


class EveryNote(unittest.TestCase):
    def test_every_note_turns_on_scholar_tags_with_its_own_address(self) -> None:
        notes = sorted(ROOT.glob("notes/*/index.qmd"))
        self.assertTrue(notes, "no notes found")
        for page in notes:
            with self.subTest(note=page.parent.name):
                self.assertEqual(search_problems(page.parent.name, page.read_text()), [])

    def test_a_note_missing_them_is_named(self) -> None:
        # The control: a front matter without them, and one whose address names another folder.
        self.assertIn("google-scholar: true", search_problems("a-note", "---\ntitle: A note\n---\n"))
        moved = "---\ngoogle-scholar: true\nappendix-cite-as: false\ncitation:\n  type: report\n"
        moved += f"  publisher: haau3 Research\n  url: {SITE}/notes/old-name/\n---\n"
        self.assertEqual(search_problems("new-name", moved), [f"citation url {SITE}/notes/new-name/"])


class TheSite(unittest.TestCase):
    def test_link_previews_and_canonical_addresses_on_every_page(self) -> None:
        config = (ROOT / "_quarto.yml").read_text().splitlines()
        for line in ("  open-graph: true", "  twitter-card: true", "    canonical-url: true"):
            with self.subTest(line=line.strip()):
                self.assertIn(line, config)

    def test_scholar_tags_are_not_switched_on_for_every_page(self) -> None:
        config = (ROOT / "_quarto.yml").read_text()
        self.assertNotIn("google-scholar", config.replace("Google Scholar", ""))


if __name__ == "__main__":
    unittest.main()

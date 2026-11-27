"""Every note can be found: it turns on Google Scholar's citation tags and gives the citation they are
filled from, with its own address; and the site gives every page link previews and a canonical address.
Google Scholar's tags stay off the other pages, so they never look like papers. Once a note's release
has its DOI, the front matter and the "How to cite" section give the same one.

    uv run python -m unittest discover -s tests -p test_note_search.py
"""

from __future__ import annotations

import re
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


def doi_problems(text: str) -> list[str]:
    """What is wrong with a note's DOI, by name. A note has none until its release is archived."""
    found = re.search(r"^doi: (\S+)$", front_matter(text), re.M)
    if not found:
        return []
    doi = found.group(1)
    problems = [] if re.fullmatch(r"10\.5281/zenodo\.\d+", doi) else ["doi is a Zenodo DOI"]
    cite = text.partition("\n## How to cite")[2].partition("\n## ")[0]
    if f"<https://doi.org/{doi}>" not in cite:
        problems.append("How to cite links the same DOI")
    return problems


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


    def test_a_note_cites_the_doi_its_front_matter_gives(self) -> None:
        for page in sorted(ROOT.glob("notes/*/index.qmd")):
            with self.subTest(note=page.parent.name):
                self.assertEqual(doi_problems(page.read_text()), [])

    def test_a_doi_the_citation_does_not_give_is_named(self) -> None:
        # The controls: a note before its release passes, and one citing an older DOI is named.
        self.assertEqual(doi_problems("---\ntitle: A note\n---\n\n## How to cite\n\nFung.\n"), [])
        older = "---\ndoi: 10.5281/zenodo.2\n---\n\n## How to cite\n\n<https://doi.org/10.5281/zenodo.1>\n"
        self.assertEqual(doi_problems(older), ["How to cite links the same DOI"])

    def test_the_header_labels_it_doi(self) -> None:
        # Quarto's own label is "Doi".
        self.assertIn("doi-title: DOI", (ROOT / "notes" / "_metadata.yml").read_text().splitlines())


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

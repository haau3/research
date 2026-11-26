"""CITATION.cff loads the way Zenodo loads it, so a release is archived and gets its DOI.

Zenodo reads the file with yaml.safe_load and its CitationMetadataSchema (zenodo-rdm,
site/zenodo_rdm/vcs/schemas.py): title, abstract, message and license are each one text value,
keywords a list of text, and authors a list of names. Anything else fails the release with
"Citation metadata load failed." (measured 2026-11-26: v0.1.0 failed on a list of two licences).

    uv run python -m unittest discover -s tests -p test_citation.py
"""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def zenodo_problems(data: dict) -> list[str]:
    """Each field Zenodo reads whose shape its importer would refuse, by name."""
    problems = []
    title = data.get("title")
    if not (isinstance(title, str) and len(title) >= 3):
        problems.append("title is one text value of at least 3 characters")
    for name in ("abstract", "message", "license"):
        if name in data and not isinstance(data[name], str):
            problems.append(f"{name} is one text value")
    keywords = data.get("keywords", [])
    if not (isinstance(keywords, list) and all(isinstance(k, str) for k in keywords)):
        problems.append("keywords is a list of text")
    authors = data.get("authors", [])
    names = ("family-names", "given-names", "affiliation", "orcid")
    if not (
        isinstance(authors, list)
        and authors
        and all(isinstance(a, dict) and all(isinstance(a.get(n, ""), str) for n in names) for a in authors)
    ):
        problems.append("authors is a list of names")
    return problems


class Citation(unittest.TestCase):
    def test_zenodo_can_load_it(self) -> None:
        data = yaml.safe_load((ROOT / "CITATION.cff").read_text())
        self.assertEqual(zenodo_problems(data), [])

    def test_two_licences_are_caught(self) -> None:
        # The control: the file as v0.1.0 had it, which Zenodo refused.
        data = yaml.safe_load((ROOT / "CITATION.cff").read_text())
        data["license"] = ["MIT", "CC-BY-4.0"]
        self.assertEqual(zenodo_problems(data), ["license is one text value"])


if __name__ == "__main__":
    unittest.main()

"""The Library: its check refuses each kind of bad entry and passes a good one, its pages list every
approved entry and never a draft, its entries are a bibliography pandoc reads, and its search and
drafting scripts treat what they read as they say. Nothing here calls the network or a model.

    uv run python -m unittest discover -s tests -p test_library.py
"""

from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import library  # noqa: E402
import library_draft  # noqa: E402
import library_search  # noqa: E402

THEMES = {t["id"] for t in library.themes()}
VALIDATOR = library.csl_validator()
SUMMARY = "The survey found that a quarter of caregivers who use chatbots use them for caregiving."


def approved_entry(**changes) -> dict:
    """One good, approved entry. Each test changes what its case needs."""
    entry = {
        "id": "rivera2026caregivers",
        "type": "article-journal",
        "title": "Caregivers and chatbots: a test record",
        "author": [{"family": "Rivera", "given": "Ana"}, {"literal": "A Study Group"}],
        "issued": {"date-parts": [[2026, 9, 23]]},
        "container-title": "A Journal",
        "DOI": "10.1234/test.1",
        "PMID": "12345678",
        "custom": {
            "summary": SUMMARY,
            "themes": ["caregiving-and-ai"],
            "evidence": "survey",
            "studied": "adults caring for another adult",
            "sample": {"n": 1300, "of": "caregivers"},
            "peer-reviewed": True,
            "read": "abstract",
            "headings": [{"id": "D017028", "name": "Caregivers", "major": True}],
            "checked": "2026-11-27",
            "found": "search 2026-11-27",
            "drafted-by": "claude-opus-5-5",
            "approved": "2026-11-28",
            "approved-summary": library.fingerprint(SUMMARY),
        },
    }
    entry.update({k: v for k, v in changes.items() if k != "custom"})
    entry["custom"].update(changes.get("custom", {}))
    return entry


def draft_entry(**changes) -> dict:
    entry = approved_entry(id="chen2025voice", DOI="10.1234/test.2", PMID="87654321", title="A voice assistant for carers")
    for key in ("approved", "approved-summary"):
        entry["custom"].pop(key)
    entry["custom"]["draft"] = True
    entry["custom"]["review-note"] = "Check the sample."
    entry.update({k: v for k, v in changes.items() if k != "custom"})
    entry["custom"].update(changes.get("custom", {}))
    return entry


def problems(entry: dict) -> list[str]:
    return library.entry_problems(entry, THEMES, VALIDATOR)


class TheCheck(unittest.TestCase):
    def test_the_repositorys_own_library_passes(self) -> None:
        self.assertEqual(library.check_all(), [])

    def test_a_good_approved_entry_and_a_good_draft_pass(self) -> None:
        # The controls for every refusal below.
        self.assertEqual(problems(approved_entry()), [])
        self.assertEqual(problems(draft_entry()), [])

    def test_each_missing_required_field_is_named(self) -> None:
        for field in ("id", "type", "title", "author", "issued"):
            with self.subTest(field=field):
                entry = approved_entry()
                entry.pop(field)
                self.assertIn(f"missing {field}", problems(entry))
        entry = approved_entry()
        entry.pop("DOI")
        self.assertIn("missing DOI or URL", problems(entry))
        self.assertEqual(problems(approved_entry(URL="https://example.org/report")), [])
        for field in library.CUSTOM_REQUIRED:
            with self.subTest(field=field):
                entry = approved_entry()
                entry["custom"].pop(field)
                self.assertIn(f"missing custom.{field}", problems(entry))

    def test_an_entry_with_no_approval_is_named(self) -> None:
        entry = approved_entry()
        for key in ("approved", "approved-summary"):
            entry["custom"].pop(key)
        self.assertIn("is neither a draft nor approved", problems(entry))

    def test_a_draft_that_is_also_approved_is_named(self) -> None:
        entry = approved_entry(custom={"draft": True})
        self.assertIn("is both a draft and approved", problems(entry))

    def test_a_summary_changed_after_its_approval_is_named(self) -> None:
        entry = approved_entry(custom={"summary": SUMMARY + " One more sentence."})
        self.assertIn("its summary has changed since it was approved", problems(entry))

    def test_an_abstract_anywhere_is_named(self) -> None:
        self.assertIn("carries an abstract (summaries are in our own words)", problems(approved_entry(abstract="Theirs.")))
        inside = problems(approved_entry(custom={"abstract": "Theirs."}))
        self.assertIn("carries an abstract (summaries are in our own words)", inside)
        self.assertIn("unknown field custom.abstract", inside)

    def test_a_field_csl_does_not_define_is_refused_by_its_schema(self) -> None:
        found = problems(approved_entry(journal="A Journal"))
        self.assertTrue(any(p.startswith("CSL schema") and "journal" in p for p in found), found)

    def test_values_outside_the_librarys_lists_are_named(self) -> None:
        cases = {
            "custom.themes: unknown theme 'gardening'": {"themes": ["gardening"]},
            "custom.evidence: 'anecdote' is not a kind of evidence the Library uses": {"evidence": "anecdote"},
            "custom.read is not 'full' or 'abstract'": {"read": "skimmed"},
            "custom.peer-reviewed is not true or false": {"peer-reviewed": "yes"},
            "custom.checked is not a date (YYYY-MM-DD)": {"checked": "last week"},
            "custom.found is neither 'seed' nor 'search YYYY-MM-DD'": {"found": "a friend"},
            "custom.sample is not {n: a whole number, of: what was counted}": {"sample": {"n": "many", "of": "people"}},
        }
        for expected, change in cases.items():
            with self.subTest(expected=expected):
                self.assertIn(expected, problems(approved_entry(custom=change)))
        bad_heading = problems(approved_entry(custom={"headings": [{"id": "caregivers", "name": "Caregivers", "major": False}]}))
        self.assertTrue(any("is not an NLM heading" in p for p in bad_heading))

    def test_the_same_paper_twice_is_named(self) -> None:
        twice = [approved_entry(), approved_entry(id="rivera2026caregiversb", DOI="10.1234/TEST.1")]
        found = library.check_entries(twice, THEMES)
        self.assertTrue(any("doi:10.1234/test.1 is also" in p for p in found), found)
        self.assertEqual(library.check_entries([approved_entry(), draft_entry()], THEMES), [])

    def test_a_screened_out_paper_needs_a_listed_reason(self) -> None:
        good = {"title": "A paper", "pmid": "1", "reason": library.REASONS[0], "note": "", "search": "2026-11-27"}
        self.assertEqual(library.check_screened_out([good]), [])
        self.assertTrue(library.check_screened_out([dict(good, reason="did not like it")]))
        self.assertTrue(library.check_screened_out([{k: v for k, v in good.items() if k != "pmid"}]))


class ThePages(unittest.TestCase):
    area = {"id": "caregiving-and-ai", "theme": "caregiving-and-ai", "title": "Caregiving and AI"}
    names = {t["id"]: t["name"] for t in library.themes()}

    def test_an_area_lists_its_approved_entries_and_never_a_draft(self) -> None:
        entries = [approved_entry(), draft_entry()]
        listed, drafts = library.area_entries(self.area, entries, drafts=False)
        self.assertEqual([e["id"] for e in listed], ["rivera2026caregivers"])
        self.assertEqual(drafts, [])
        page = library.entries_section(listed, drafts, self.names, self.area)
        self.assertIn('id="entry-rivera2026caregivers"', page)
        self.assertNotIn("chen2025voice", page)

    def test_the_review_view_shows_drafts_first_and_numbered(self) -> None:
        listed, drafts = library.area_entries(self.area, [approved_entry(), draft_entry()], drafts=True)
        page = library.entries_section(listed, drafts, self.names, self.area)
        self.assertLess(page.index("chen2025voice"), page.index("rivera2026caregivers"))
        self.assertIn("Draft 1, for review.", page)

    def test_the_built_pages_list_every_approved_entry_and_no_draft(self) -> None:
        library.build(drafts=False)
        entries = library.load(library.REFERENCES)
        for area in library.areas():
            page = (library.LIBRARY / area["id"] / "_entries.md").read_text()
            for entry in entries:
                if area["theme"] not in entry["custom"]["themes"]:
                    continue
                with self.subTest(entry=entry["id"]):
                    if library.is_approved(entry):
                        self.assertIn(f'id="entry-{entry["id"]}"', page)
                    else:
                        self.assertNotIn(f'id="entry-{entry["id"]}"', page)

    def test_newest_first(self) -> None:
        older = approved_entry(id="a", issued={"date-parts": [[2024]]})
        newer = approved_entry(id="b", issued={"date-parts": [[2026, 1, 2]]})
        self.assertEqual([e["id"] for e in library.newest_first([older, newer])], ["b", "a"])

    def test_others_words_are_escaped(self) -> None:
        entry = approved_entry(title="A <script>x</script> and $T^2$ test")
        html = library.entry_html(entry, self.names)
        self.assertNotIn("<script>x", html)
        self.assertIn("&lt;script&gt;", html)

    def test_each_entry_is_marked_up_as_a_scholarly_article_with_its_identifiers(self) -> None:
        report = approved_entry(id="network2026survey", type="report", URL="https://example.org/r", publisher="A Network")
        report.pop("DOI")
        report.pop("PMID")
        report.pop("container-title")
        block = library.json_ld(self.area, [approved_entry(), report], self.names)
        data = json.loads(re.search(r"<script type=\"application/ld\+json\">\n(.*)\n</script>", block, re.S).group(1))
        self.assertEqual(data["@type"], "CollectionPage")
        items = [i["item"] for i in data["mainEntity"]["itemListElement"]]
        self.assertEqual([i["@type"] for i in items], ["ScholarlyArticle", "Report"])
        self.assertIn({"@type": "PropertyValue", "propertyID": "DOI", "value": "10.1234/test.1"}, items[0]["identifier"])
        self.assertIn("D017028", [t["termCode"] for t in items[0]["about"]])
        # Our summary is never given as the work's own description.
        self.assertNotIn(SUMMARY, block)

    def test_library_pages_never_turn_on_scholar_tags(self) -> None:
        for page in [library.LIBRARY / "index.qmd", *library.LIBRARY.glob("*/index.qmd")]:
            with self.subTest(page=page.name):
                self.assertNotIn("google-scholar", page.read_text())


class ApprovingAndDropping(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder)
        self.references = self.folder / "references.json"
        self.screened = self.folder / "screened-out.json"
        library.dump(self.references, [approved_entry(), draft_entry()])
        library.dump(self.screened, [])
        for name, path in (("REFERENCES", self.references), ("SCREENED_OUT", self.screened)):
            patcher = mock.patch.object(library, name, path)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_approving_a_draft_records_the_day_and_its_summary(self) -> None:
        self.assertEqual(library.approve(["chen2025voice"], "2026-11-29"), [])
        entry = next(e for e in library.load(self.references) if e["id"] == "chen2025voice")
        self.assertEqual(entry["custom"]["approved"], "2026-11-29")
        self.assertEqual(entry["custom"]["approved-summary"], library.fingerprint(SUMMARY))
        self.assertNotIn("draft", entry["custom"])
        self.assertNotIn("review-note", entry["custom"])
        self.assertEqual(problems(entry), [])

    def test_only_a_draft_can_be_approved(self) -> None:
        self.assertEqual(library.approve(["rivera2026caregivers"], "2026-11-29"), ["rivera2026caregivers: not a draft"])
        self.assertEqual(library.approve(["nobody"], "2026-11-29"), ["nobody: no such entry"])

    def test_dropping_a_draft_moves_it_to_the_screened_out_list(self) -> None:
        self.assertEqual(library.drop(["chen2025voice"], "out of scope on reading", "2026-11-29"), [])
        self.assertEqual([e["id"] for e in library.load(self.references)], ["rivera2026caregivers"])
        row = library.load(self.screened)[0]
        self.assertEqual((row["doi"], row["reason"], row["note"]), ("10.1234/test.2", "removed on review", "out of scope on reading"))
        self.assertEqual(library.check_screened_out([row]), [])


class AsABibliography(unittest.TestCase):
    """A note cites from the Library's file, so pandoc must read every entry in it."""

    def cite(self, bibliography: Path, markdown: str) -> str:
        quarto = shutil.which("quarto")
        self.assertIsNotNone(quarto, "quarto is not on PATH (uv run puts it there)")
        result = subprocess.run(
            [quarto, "pandoc", "-f", "markdown", "-t", "plain", "--citeproc", f"--bibliography={bibliography}"],
            input=markdown, capture_output=True, text=True, check=True,
        )
        return result.stdout

    def test_an_entry_is_cited_with_our_fields_beside_it(self) -> None:
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder)
        library.dump(folder / "references.json", [approved_entry()])
        out = self.cite(folder / "references.json", "As shown [@rivera2026caregivers].\n")
        self.assertIn("(Rivera and A Study Group 2026)", out)
        self.assertIn("Caregivers and Chatbots", out)

    def test_every_entry_in_the_library_is_read(self) -> None:
        entries = library.load(library.REFERENCES)
        if not entries:
            self.skipTest("the Library holds no entry yet")
        out = self.cite(library.REFERENCES, "---\nnocite: '@*'\n---\n\nAll of them.\n")
        for entry in entries[:50]:
            with self.subTest(entry=entry["id"]):
                self.assertIn(str(library.date_parts(entry)[0]), out)


class TheSearch(unittest.TestCase):
    def test_a_sources_record_is_cut_to_csl_and_loses_its_abstract(self) -> None:
        crossref = {
            "type": "journal-article", "title": "A <i>tagged</i> title", "abstract": "<jats:p>Theirs</jats:p>",
            "author": [{"family": "Rivera", "given": "Ana", "ORCID": "x", "sequence": "first"}],
            "issued": {"date-parts": [[2026, 9]]}, "DOI": "10.1234/x", "volume": 21, "reference": [1, 2], "funder": [],
        }
        csl = library_search.clean_csl(crossref)
        self.assertEqual(csl["type"], "article-journal")
        self.assertEqual(csl["title"], "A tagged title")
        self.assertEqual(csl["author"], [{"family": "Rivera", "given": "Ana"}])
        self.assertEqual(csl["volume"], "21")
        self.assertNotIn("abstract", csl)
        self.assertNotIn("reference", csl)
        self.assertEqual(list(VALIDATOR.iter_errors(dict(csl, id="x"))), [])

    def test_a_citation_key_a_person_can_read(self) -> None:
        csl = {"author": [{"family": "van der Berg"}], "issued": {"date-parts": [[2026]]}, "title": "The caregivers' chatbot"}
        self.assertEqual(library_search.entry_id(csl, set()), "van2026caregivers")
        self.assertEqual(library_search.entry_id(csl, {"van2026caregivers"}), "van2026caregiversa")

    def candidate(self, key: str, source: str, title: str, doi: str | None = None, **extra) -> dict:
        csl = {"title": title, "DOI": doi} if doi else {"title": title}
        if source == "arXiv":
            csl.update({"publisher": "arXiv", "number": key.split(":", 1)[1]})
        if key.startswith("pmid:"):
            csl["PMID"] = key.split(":", 1)[1]
        return {"key": key, "source": source, "csl": csl, **extra}

    def test_a_paper_held_or_set_aside_is_not_offered_again(self) -> None:
        found = [self.candidate("pmid:1", "PubMed", "One"), self.candidate("pmid:2", "PubMed", "Two")]
        kept = library_search.new_only(found, {"pmid:1"})
        self.assertEqual([c["key"] for c in kept], ["pmid:2"])
        self.assertEqual(library_search.new_only(found, set(), {"two"}), [found[0]])

    def test_a_published_paper_is_kept_over_its_preprint(self) -> None:
        journal = self.candidate("pmid:3", "PubMed", "Who Is Asking?", doi="10.1/j")
        by_doi = self.candidate("arxiv:2601.00001", "arXiv", "An older title", journal_doi="10.1/J")
        by_title = self.candidate("arxiv:2601.00002", "arXiv", "Who is asking")
        in_pubmed = self.candidate("pmid:4", "PubMed", "Who is asking?", doi="10.1101/x", pubtypes=["Preprint"])
        # Whatever the order the databases return them in.
        kept = library_search.new_only([in_pubmed, by_title, by_doi, journal], set())
        self.assertEqual([c["key"] for c in kept], ["pmid:3"])

    def test_pubmeds_record_is_read_for_headings_and_retraction(self) -> None:
        xml = b"""<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>9</PMID><Article>
          <Journal><Title>A Journal</Title></Journal><ArticleTitle>A <i>title</i></ArticleTitle>
          <Abstract><AbstractText Label="AIM">To test.</AbstractText></Abstract><Language>eng</Language>
          <PublicationTypeList><PublicationType>Journal Article</PublicationType><PublicationType>Retracted Publication</PublicationType></PublicationTypeList>
          </Article><MeshHeadingList>
          <MeshHeading><DescriptorName UI="D017028" MajorTopicYN="N">Caregivers</DescriptorName><QualifierName UI="Q1" MajorTopicYN="Y">psychology</QualifierName></MeshHeading>
          <MeshHeading><DescriptorName UI="D000368" MajorTopicYN="N">Aged</DescriptorName></MeshHeading>
          </MeshHeadingList></MedlineCitation><PubmedData><ArticleIdList>
          <ArticleId IdType="doi">10.1/x</ArticleId><ArticleId IdType="pmc">PMC1</ArticleId></ArticleIdList></PubmedData>
          </PubmedArticle></PubmedArticleSet>"""
        with mock.patch.object(library_search, "ncbi", return_value=xml):
            record = library_search.pubmed_records(["9"])[0]
        self.assertEqual(record["title"], "A title")
        self.assertEqual(record["abstract"], "AIM: To test.")
        self.assertEqual(record["headings"], [
            {"id": "D017028", "name": "Caregivers", "major": True},
            {"id": "D000368", "name": "Aged", "major": False},
        ])
        self.assertTrue(record["retracted"])
        self.assertEqual((record["doi"], record["pmcid"]), ("10.1/x", "PMC1"))

    def test_a_pages_text_leaves_out_code_and_references(self) -> None:
        page = '<html><script>var x</script><p>Kept.</p><section class="ltx_bibliography"><p>Cited.</p></section></html>'
        self.assertEqual(library_search.html_text(page), "Kept.")
        jats = b"<article><front><p>Front.</p></front><body><title>Methods</title><p>Body.</p></body><back><p>Refs.</p></back></article>"
        self.assertEqual(library_search.jats_text(jats), "Methods\nBody.")


class TheDrafting(unittest.TestCase):
    def test_eight_words_from_the_abstract_are_found(self) -> None:
        abstract = "Of the 487 respondents who reported being AI chatbot users, a quarter used it."
        self.assertEqual(
            library_draft.repeated("They said: of the 487 respondents who reported being AI chatbot, so.", abstract),
            "of the 487 respondents who reported being ai",
        )
        # The control: seven words in common are not a copy.
        self.assertEqual(library_draft.repeated("Of the 487 respondents who reported being, in all.", abstract), "")

    def test_an_answer_is_read_only_when_the_model_finished(self) -> None:
        def message(stop: str, text: str = "{}") -> SimpleNamespace:
            return SimpleNamespace(stop_reason=stop, content=[SimpleNamespace(type="text", text=text)])

        self.assertEqual(library_draft.answer(message("end_turn", '{"a": 1}')), ({"a": 1}, ""))
        self.assertEqual(library_draft.answer(message("refusal")), (None, "the model declined"))
        self.assertEqual(library_draft.answer(message("max_tokens")), (None, "the answer was cut off"))
        self.assertEqual(library_draft.answer(message("end_turn", "not json")), (None, "the answer was not valid JSON"))

    def test_a_drafted_answer_becomes_a_valid_draft(self) -> None:
        candidate = {
            "key": "pmid:12345678",
            "csl": {k: v for k, v in approved_entry().items() if k not in ("id", "custom")},
            "headings": [{"id": "D017028", "name": "Caregivers", "major": True}],
        }
        fields = {
            "summary": "  A summary\nin our words.  ", "themes": ["dementia-caregiving", "caregiving-and-ai"],
            "evidence": "survey", "studied": "carers", "sample_n": 0, "sample_of": "", "peer_reviewed": True,
            "review_note": "",
        }
        area = {"theme": "caregiving-and-ai"}
        entry = library_draft.entry_from(candidate, fields, "abstract", area, "2026-11-27", set(), ["Borderline: check it."])
        self.assertEqual(problems(entry), [])
        self.assertTrue(library.is_draft(entry))
        self.assertEqual(entry["custom"]["themes"], ["caregiving-and-ai", "dementia-caregiving"])
        self.assertEqual(entry["custom"]["summary"], "A summary in our words.")
        self.assertNotIn("sample", entry["custom"])
        self.assertEqual(entry["custom"]["review-note"], "Borderline: check it.")

    def test_a_pubmed_record_with_no_doi_links_its_pubmed_page(self) -> None:
        csl = {k: v for k, v in approved_entry().items() if k not in ("id", "custom", "DOI")}
        fields = {
            "summary": "A summary.", "themes": ["caregiving-and-ai"], "evidence": "survey", "studied": "carers",
            "sample_n": 0, "sample_of": "", "peer_reviewed": True, "review_note": "",
        }
        entry = library_draft.entry_from({"key": "pmid:12345678", "csl": csl}, fields, "abstract", {"theme": "caregiving-and-ai"}, "2026-11-27", set(), [])
        self.assertEqual(entry["URL"], "https://pubmed.ncbi.nlm.nih.gov/12345678/")
        self.assertEqual(problems(entry), [])

    def test_the_prompt_has_every_blank_filled(self) -> None:
        area = library_search.area_by_id("caregiving-and-ai")
        text = library_draft.system_prompt(area, library.themes(), "2026-11-27")
        self.assertIsNone(re.search(r"\{[a-z_]+\}", text), "a blank is left unfilled")
        self.assertIn("November 27, 2026", text)
        self.assertIn(area["exclude"][0], text)

    def test_the_cost_is_computed_from_the_tokens_at_the_committed_prices(self) -> None:
        usage = library_draft.Usage()
        tokens = SimpleNamespace(input_tokens=1_000_000, cache_creation_input_tokens=0, cache_read_input_tokens=1_000_000, output_tokens=100_000)
        usage.add(tokens, batch=False)
        usage.add(copy.copy(tokens), batch=True)
        # Standard: 4 + 0.2 + 2 = 6.2 dollars; the batch half of that: 3.1.
        self.assertAlmostEqual(usage.cost(), 9.3)

    def test_a_request_id_the_batch_api_accepts(self) -> None:
        for key in ("pmid:12345", "arxiv:2605.29473", "url:https://example.org/a report.pdf"):
            with self.subTest(key=key):
                self.assertRegex(library_draft.custom_id(key), r"^[A-Za-z0-9_-]{1,64}$")


if __name__ == "__main__":
    unittest.main()

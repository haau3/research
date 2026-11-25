"""scripts/check_public_text.py finds each kind of text this repository never publishes, and allows the
shapes its own text uses. The samples are made up.

    python3 -I -m unittest discover -s tests
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from check_public_text import check, commit_range, term_rules  # noqa: E402


def found(text: str) -> list[str]:
    return [f.match for f in check("sample", text)]


class FindsEachKind(unittest.TestCase):
    def test_each_kind(self) -> None:
        for text, match in [
            ("see haau3/private-thing for why", "haau3/private-thing"),
            ("at https://github.com/haau3/private-thing/issues", "github.com/haau3/private-thing"),
            ("git@github.com:haau3/private-thing.git", "github.com:haau3/private-thing.git"),
            ("it runs in haau3-example-project", "haau3-example-project"),
            ("decided in issue #123", "#123"),
            ("see issue 123", "issue 123"),
            ("this is ABC-4", "ABC-4"),
            ("part of the milestone", "milestone"),
            ("on the roadmap", "roadmap"),
            ("the owner approves it", "the owner"),
            ("once it has rolled out", "rolled out"),
            ("the key is an org secret", "org secret"),
            ("Iv23liEXAMPLEexample00", "Iv23liEXAMPLEexample00"),
            ("sk-ant-api03-EXAMPLEexampleEXAMPLE00", "sk-ant-api03-EXAMPLEexampleEXAMPLE00"),
            ("hf_EXAMPLEexampleEXAMPLEexampleEXAMPLE", "hf_EXAMPLEexampleEXAMPLEexampleEXAMPLE"),
            ("write to someone@personal.example-mail.net", "someone@personal.example-mail.net"),
        ]:
            with self.subTest(text=text):
                self.assertIn(match, found(text))


class AllowsItsOwnText(unittest.TestCase):
    def test_what_a_research_page_says(self) -> None:
        text = "\n".join(
            [
                "Text is CC BY 4.0 (CC-BY-4.0, or CC-BY-SA-4.0 for a source that asks for it); code is MIT.",
                "Scored on GPT-5, Gemma 3 and Claude, with PHQ-9, SF-36, EQ-5D and COVID-19 as examples.",
                "The source is https://github.com/haau3/research, and haau3/research#3 is a pull request.",
                "The SDKs are haau3-node, haau3-python and haau3-go, at https://github.com/haau3/haau3-node.",
                "Write to hello@haau3.com. Co-Authored-By: Claude <noreply@anthropic.com>",
                "The workflow builds the site and deploys it to GitHub Pages.",
            ]
        )
        self.assertEqual(found(text), [])

    def test_a_link_to_this_repositorys_own_pull_request_only(self) -> None:
        self.assertEqual(found("([#5](https://github.com/haau3/research/issues/5))"), [])
        self.assertEqual(found("([#5](https://github.com/haau3/haau3-node/issues/5))"), ["#5"])

    def test_a_name_that_only_starts_like_a_public_one(self) -> None:
        # The controls: an organisation name that is not one of the public repositories.
        self.assertEqual(found("see haau3/research-private"), ["haau3/research-private"])
        self.assertEqual(found("see haau3/haau3-client"), ["haau3/haau3-client", "haau3-client"])

    def test_a_colour_written_as_a_stylesheet_writes_it(self) -> None:
        self.assertEqual(found('$navbar-bg: #171614;\nnight: "#171614"\ncream: \'#123\''), [])
        # The controls: the same digits in a sentence, and a length no colour has.
        self.assertEqual(found("fixed in #171614 last week"), ["#171614"])
        self.assertEqual(found('see "#12345"'), ["#12345"])

    def test_an_id_with_a_standard_prefix_only(self) -> None:
        self.assertEqual(found("GPT-5 and BY-4"), [])
        self.assertEqual(found("GPTX-5 and BYE-4"), ["GPTX-5", "BYE-4"])


class Options(unittest.TestCase):
    def test_extra_terms_from_a_file_of_patterns_with_comments(self) -> None:
        rules = term_rules("# a comment\nsecret-codename\n\n  other\\d+  # trailing comment\n")
        self.assertEqual([r.pattern.pattern for r in rules], ["secret-codename", "other\\d+"])
        self.assertEqual(
            [f.match for f in check("sample", "the Secret-Codename and other42", rules)],
            ["Secret-Codename", "other42"],
        )

    def test_a_repositorys_first_push_checks_every_commit(self) -> None:
        self.assertEqual(commit_range("no-such-ref-anywhere..HEAD"), "HEAD")
        self.assertEqual(commit_range("HEAD..HEAD"), "HEAD..HEAD")


class ThisRepository(unittest.TestCase):
    def test_its_own_files_pass(self) -> None:
        # The real run, as a pull request uses it: it fails on any finding.
        result = subprocess.run(
            [sys.executable, "-I", "scripts/check_public_text.py"], cwd=ROOT, capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()

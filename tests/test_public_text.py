"""scripts/check_public_text.py finds each kind of text this repository never publishes, and allows the
shapes its own text uses. The samples are made up.

    python3 -I -m unittest discover -s tests
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from check_public_text import CREDENTIALS, LOG_RULES, check, check_log, commit_range, term_rules  # noqa: E402


def found(text: str) -> list[str]:
    return [f.match for f in check("sample", text)]


class EveryKindOfCredential(unittest.TestCase):
    """A rule's quick first pass skips a line only where the rule could not match. One made-up sample of
    each kind of credential, put together here so the file itself never holds one."""

    SAMPLES = [
        "-----BEGIN " + "RSA PRIVATE KEY-----",
        "gh" + "p_" + "EXAMPLE" * 5,
        "github" + "_pat_" + "EXAMPLE" * 4,
        "npm" + "_" + "example" * 5 + "z",
        "pypi-" + "AgEIcHlwaS5vcmc" + "EXAMPLE" * 4,
        "Iv1." + "0123456789abcdef",
        "sk" + "_live_" + "EXAMPLE" * 2,
        "sk-" + "ant-" + "EXAMPLE" * 4,
        "sk-" + "proj-" + "EXAMPLE" * 6,
        "AI" + "za" + "EXAMPLE" * 5,
        "hf" + "_" + "EXAMPLE" * 5,
        "xo" + "xb-" + "EXAMPLE" * 2,
    ]

    def test_one_sample_for_each_kind(self) -> None:
        self.assertEqual(len(self.SAMPLES), len(CREDENTIALS))

    def test_each_is_found_in_text_and_in_a_log(self) -> None:
        for sample in self.SAMPLES:
            with self.subTest(sample=sample[:12]):
                self.assertEqual(found(f"the key was {sample} here"), [sample])
                self.assertEqual([f.match for f in check("log", f'{{"output": "{sample}"}}', LOG_RULES)], [sample])


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

    def test_a_doi_whose_publisher_code_looks_like_an_id(self) -> None:
        # A journal's DOI suffix, on its own, quoted in JSON and in a link.
        doi = '10.1200/CCI-25-00044\n"DOI": "10.1200/GO-25-00647",\n<https://doi.org/10.1200/CCI-25-00042>'
        self.assertEqual(found(doi), [])
        # The controls: the same code outside a DOI, and after a DOI in the same sentence.
        self.assertEqual(found("Fixed in CCI-25."), ["CCI-25"])
        self.assertEqual(found("See 10.1200/x for CCI-25."), ["CCI-25"])


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


@unittest.skipUnless(hasattr(zipfile, "ZIP_ZSTANDARD"), "Inspect's logs are Zstandard zips, which Python reads from 3.14")
class InsideALog(unittest.TestCase):
    """An Inspect log is a Zstandard zip of JSON. A credential or a path from the computer that ran it is
    found anywhere inside; private terms are found in what the run wrote (its settings and metadata),
    and a benchmark's or a model's own words are not held to the rules for this repository's words."""

    # Random text from a model's encrypted reasoning that happens to look like an app's client id.
    LOOKALIKE = "Iv46G3KtIKgKzu0d8cXr9vfJ7s31LItGRebhx"

    def log(self, sample: dict, header: dict | None = None) -> str:
        path = Path(tempfile.mkdtemp()) / "run.eval"
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_ZSTANDARD) as log:
            log.writestr("header.json", json.dumps(header or {"eval": {"model": "openai/gpt-6-luna"}}))
            log.writestr("samples/1_epoch_1.json", json.dumps(sample))
        return str(path)

    def test_a_credential_inside_a_log_is_found(self) -> None:
        findings = check_log(self.log({"output": "the key was sk-ant-api03-EXAMPLEexampleEXAMPLE00"}), LOG_RULES)
        self.assertEqual([f.match for f in findings], ["sk-ant-api03-EXAMPLEexampleEXAMPLE00"])
        self.assertTrue(findings[0].where.endswith("run.eval:samples/1_epoch_1.json:1"), findings[0].where)

    def test_a_models_words_are_not_held_to_this_repositorys(self) -> None:
        # The control: the same words in a file of this repository are found.
        words = "Ask the owner of the roadmap about issue 12."
        self.assertEqual(check_log(self.log({"output": words}), LOG_RULES), [])
        self.assertNotEqual(found(words), [])

    def test_a_private_term_in_what_the_run_wrote_is_found(self) -> None:
        header = {"eval": {"metadata": {"note": "about Secret-Codename"}}}
        findings = check_log(self.log({"output": "fine"}, header), LOG_RULES, term_rules("secret-codename\n"))
        self.assertEqual([f.match for f in findings], ["Secret-Codename"])
        self.assertTrue(findings[0].where.endswith("run.eval:header.json:1"), findings[0].where)

    def test_a_private_term_in_a_benchmarks_words_is_not(self) -> None:
        # A word in the benchmark's conversation that happens to be a private term of ours.
        sample = {"input": "Is Secret-Codename safe to take with food?"}
        self.assertEqual(check_log(self.log(sample), LOG_RULES, term_rules("secret-codename\n")), [])

    def test_encrypted_reasoning_is_left_out(self) -> None:
        block = {"type": "reasoning", "redacted": True, "reasoning": f"CAQS/{self.LOOKALIKE}/h4p", "summary": ""}
        self.assertEqual(check_log(self.log({"messages": [{"content": [block]}]}), LOG_RULES), [])
        # The control: the same text where it is readable is found.
        plain = check_log(self.log({"output": f"CAQS/{self.LOOKALIKE}/h4p"}), LOG_RULES)
        self.assertEqual([f.match for f in plain], [self.LOOKALIKE])

    def test_encrypted_reasoning_kept_as_an_attachment_is_left_out(self) -> None:
        block = {"type": "reasoning", "redacted": True, "reasoning": "attachment://c077", "summary": ""}
        sample = {"messages": [{"content": [block]}], "attachments": {"c077": f"CAQS/{self.LOOKALIKE}/h4p"}}
        self.assertEqual(check_log(self.log(sample), LOG_RULES), [])
        # The control: an attachment that no encrypted reasoning points to is still read.
        other = {"messages": [], "attachments": {"d088": f"CAQS/{self.LOOKALIKE}/h4p"}}
        self.assertEqual([f.match for f in check_log(self.log(other), LOG_RULES)], [self.LOOKALIKE])

    def test_reasoning_that_is_not_encrypted_is_read(self) -> None:
        block = {"type": "reasoning", "redacted": False, "reasoning": f"I will use {self.LOOKALIKE}", "summary": ""}
        findings = check_log(self.log({"messages": [{"content": [block]}]}), LOG_RULES)
        self.assertEqual([f.match for f in findings], [self.LOOKALIKE])

    def test_a_path_from_the_computer_that_ran_it_is_found(self) -> None:
        traceback = 'File "/Users/someone/dev/research/.venv/lib/x.py", line 1'
        findings = check_log(self.log({"error": {"traceback": traceback}}), LOG_RULES)
        self.assertEqual([f.match for f in findings], ["/Users/someone/"])
        # The control: the same words inside a web address, and the path once it reads ~/, pass.
        link = "[ASGE Sedation Guidelines](https://www.asge.org/home/resources/for-patients/)"
        self.assertEqual(check_log(self.log({"output": link}), LOG_RULES), [])
        tidied = traceback.replace("/Users/someone/dev/", "~/")
        self.assertEqual(check_log(self.log({"error": {"traceback": tidied}}), LOG_RULES), [])

    def test_a_log_that_cannot_be_read_stops_the_check(self) -> None:
        path = Path(tempfile.mkdtemp()) / "broken.eval"
        path.write_bytes(b"not a zip")
        with self.assertRaises(zipfile.BadZipFile):
            check_log(str(path), LOG_RULES)


class ThisRepository(unittest.TestCase):
    def test_its_own_files_pass(self) -> None:
        # The real run, as a pull request uses it: it fails on any finding.
        result = subprocess.run(
            [sys.executable, "-I", "scripts/check_public_text.py"], cwd=ROOT, capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()

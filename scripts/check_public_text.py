"""Checks that what this repository publishes says only what this repository shows: its files, the
messages of commits not yet on main, and a pull request's title and description. Everything here is
public, so nothing in it refers to work, people, accounts or places outside this repository.

    python3 -I scripts/check_public_text.py                     the tracked files
    python3 -I scripts/check_public_text.py --commits origin/main..HEAD --text-env PR_TITLE --text-env PR_BODY
    python3 -I scripts/check_public_text.py --no-files --text-file body.md --terms <file>

--terms adds one regular expression per line (case-insensitive, `#` starts a comment), for words
that are fine to keep out of this file but must not be published either. It exits 1 on any match,
and 2 when it cannot run (a bad option or a term that is not a regular expression).

It uses only the standard library, so it runs before anything is installed. Its patterns are
compiled with re.ASCII, so \\w, \\d and \\b mean what they mean everywhere else.

An evaluation log (`.eval`, a zip of JSON) is read file by file. Most of its text is a benchmark's and
the models', so the whole log is held to two rules: no credential, and no path from the computer that
ran it. The --terms apply to the parts the run itself wrote, its settings and metadata (`header.json`
and `_journal/start.json`). A model's encrypted reasoning is left out, because its random text can
look like a credential by chance. Inspect compresses logs with Zstandard, which Python's zipfile reads
from 3.14; on an older Python a log cannot be read, and the check stops with exit 2 rather than skip it.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass

THIS_REPOSITORY = "haau3/research"


@dataclass(frozen=True)
class Rule:
    name: str
    pattern: re.Pattern[str]
    # A match this returns True for is allowed: (match, the whole text, where the match starts).
    allow: Callable[[str, str, int], bool] | None = None
    # Cheaper patterns that match wherever `pattern` could: a line none of them finds is skipped. Each
    # starts with fixed text, which Python's re searches for fast, so a run log is read in seconds.
    quick: tuple[re.Pattern[str], ...] = ()


@dataclass(frozen=True)
class Finding:
    where: str
    rule: str
    match: str


# Standards, licences, surveys and models whose names look like ticket numbers: UTF-8, CC-BY-4.0
# (read as BY-4), CC-BY-SA-4.0, GPT-5, COVID-19, SF-36, PHQ-9.
STANDARD_PREFIXES = frozenset(
    {"UTF", "SHA", "TLS", "ICD", "PHQ", "GAD", "SF", "EQ", "COVID", "GPT", "BY", "SA", "NC", "ND"}
)

# Email domains a public commit or file may name.
EMAIL_DOMAINS = re.compile(
    r"(^|\.)(haau3\.com|users\.noreply\.github\.com|anthropic\.com|example\.(com|org|net))$", re.I | re.A
)

# haau3's other public repositories, which a page may name and link to.
PUBLIC_REPOSITORY = re.compile(r"^haau3/(haau3-(node|python|go|cli)|research)(?![\w-])", re.A)
PUBLIC_URL = re.compile(r"^github\.com[:/]haau3/(haau3-(node|python|go|cli)|research)(?![\w-])", re.I | re.A)
PUBLIC_NAME = re.compile(r"^haau3-(node|python|go|cli|bot|sdk)\b", re.I | re.A)


def _changelog_link(match: str, text: str, index: int) -> bool:
    # A link to this repository's own issue or pull request.
    return (
        match.startswith("#")
        and index > 0
        and text[index - 1] == "["
        and text.startswith(f"{match}](https://github.com/{THIS_REPOSITORY}/", index)
    )


def _a_colour(match: str, text: str, index: int) -> bool:
    # A hex colour that happens to be all digits, as a stylesheet or a quoted value writes it:
    # `$navbar-bg: #171614;` or `night: "#171614"`. In a sentence the same shape is an issue number.
    if len(match) - 1 not in (3, 4, 6, 8):
        return False
    before = text[index - 1] if index > 0 else ""
    after = text[index + len(match) : index + len(match) + 1]
    return before in ("\"", "'") or after in (";", "\"", "'")


def _issue_or_colour(match: str, text: str, index: int) -> bool:
    return _changelog_link(match, text, index) or _a_colour(match, text, index)


def _standard_id(match: str, _text: str, _index: int) -> bool:
    return match.split("-")[0] in STANDARD_PREFIXES


# The kinds of credential found, each on its own so that each can also be searched for quickly.
CREDENTIALS = (
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    r"\bgh[pousr]_[A-Za-z0-9]{30,}",
    r"\bgithub_pat_\w{20,}",
    r"\bnpm_[A-Za-z0-9]{36}\b",
    r"\bpypi-AgEIcHlwaS5vcmc[\w-]{20,}",
    r"\bIv\d{1,2}[.a-zA-Z0-9]{14,}\b",
    r"\bsk_(live|test)_[A-Za-z0-9]{10,}",
    r"\bsk-ant-[\w-]{20,}",
    r"\bsk-(proj-)?[A-Za-z0-9]{32,}",
    r"\bAIza[\w-]{35}",
    r"\bhf_[A-Za-z0-9]{30,}",
    r"\bxox[abpr]-[\w-]{10,}",
)

RULES: list[Rule] = [
    # `haau3/<name>` on its own names a repository; after a `/` it is a path. A GitHub URL names one
    # wherever it is.
    Rule(
        "a repository of the haau3 organization that is not public",
        re.compile(r"(?<![@\w./-])haau3/[\w.-]+", re.A),
        lambda match, _text, _index: bool(PUBLIC_REPOSITORY.match(match)),
    ),
    Rule(
        "a link to a repository of the haau3 organization that is not public",
        re.compile(r"github\.com[:/]haau3/[\w.-]+", re.I | re.A),
        lambda match, _text, _index: bool(PUBLIC_URL.match(match)),
    ),
    Rule(
        "a haau3 project, repository or service name that is not public",
        re.compile(r"\bhaau3-[a-z0-9][\w-]*", re.I | re.A),
        lambda match, _text, _index: bool(PUBLIC_NAME.match(match)),
    ),
    Rule(
        "an issue or pull request number without the repository it belongs to",
        re.compile(r"(?<![\w/&])#\d+\b|\bissues? \d+\b", re.I | re.A),
        _issue_or_colour,
    ),
    Rule("a ticket or work-item id", re.compile(r"\b[A-Z]{2,10}-\d{1,2}[a-z]?\b", re.A), _standard_id),
    Rule("a planning word", re.compile(r"\b(milestones?|epics?|backlog|roadmap)\b", re.I | re.A)),
    Rule("a person by role", re.compile(r"\b(the owner|owner's)\b", re.I | re.A)),
    Rule("how a service is run", re.compile(r"\b(roll(ed|s)? out|production (build|release))\b", re.I | re.A)),
    Rule(
        "where secrets or settings are kept",
        re.compile(
            r"\b(org(anization)?|repo(sitory)?|actions|environment)[- ](level )?(secrets?|variables?)\b", re.I | re.A
        ),
    ),
    Rule(
        "a credential",
        re.compile("|".join(CREDENTIALS), re.A),
        # Each kind without its word boundaries matches a superset of it, so a line none finds is clean.
        quick=tuple(re.compile(part.removeprefix(r"\b").removesuffix(r"\b"), re.A) for part in CREDENTIALS),
    ),
    Rule(
        "an email address outside haau3's public ones",
        re.compile(r"[\w.+-]+@[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}\b", re.I | re.A),
        lambda match, _text, _index: bool(EMAIL_DOMAINS.search(match[match.index("@") + 1 :])),
    ),
]

# Files whose text is not this repository's own words: licence texts written by others, the fonts
# and their licence, the lockfile.
NOT_OURS = [
    re.compile(p)
    for p in (
        r"^LICENSE-CC-BY-4\.0$",
        r"^brand/fonts/",
        r"^uv\.lock$",
    )
]
# This check and its tests, which hold the patterns and their samples.
THE_CHECK = [re.compile(r"^scripts/check_public_text\.py$"), re.compile(r"^tests/test_public_text\.py$")]

# The rules a whole evaluation log is held to: no credential, and no path from the computer that ran it.
# A path that starts on its own, never one inside a web address (https://www.asge.org/home/resources/).
LAPTOP_PATH = Rule(
    "a path on the computer that ran it",
    re.compile(r"(?<![\w.:/-])/(Users|home)/[\w.-]+/", re.A),
    quick=(re.compile("/Users/"), re.compile("/home/")),
)
LOG_RULES = [*(rule for rule in RULES if rule.name == "a credential"), LAPTOP_PATH]
# The files inside a log that the run itself wrote: its settings and metadata. The --terms apply here.
LOG_OWN = re.compile(r"^(header\.json|_journal/start\.json)$")


def check_log(path: str, rules: Sequence[Rule], terms: Sequence[Rule] = ()) -> list[Finding]:
    """Every file inside an Inspect log: `rules` everywhere, `terms` in the files the run wrote. Raises
    if the log cannot be read, so it is never skipped."""
    findings: list[Finding] = []
    with zipfile.ZipFile(path) as log:
        for member in log.infolist():
            text = without_ciphertext(log.read(member).decode("utf-8", errors="replace"))
            own = LOG_OWN.match(member.filename)
            findings += check(f"{path}:{member.filename}", text, [*rules, *terms] if own else rules)
    return findings


# A model's encrypted reasoning, which it returns for the next turn and nobody can read: random text in
# which a few letters can look like a credential by chance. Everything else in a log is still checked.
CIPHERTEXT = {
    "reasoning": ("reasoning", "signature"),  # Inspect's reasoning block, when `redacted` is true
    "redacted_thinking": ("data",),  # Anthropic's raw encrypted thinking
    "thinking": ("signature",),  # Anthropic's raw thinking: its signature only
}


def without_ciphertext(text: str) -> str:
    """The text with encrypted reasoning blanked, when it is JSON; otherwise the text unchanged."""
    try:
        data = json.loads(text)
    except ValueError:
        return text

    # Inspect keeps a long string once, under `attachments`, and points to it as `attachment://<id>`.
    pointed: set[str] = set()

    def blank(node: object) -> None:
        if isinstance(node, dict):
            kind = node.get("type")
            fields = CIPHERTEXT.get(kind, ()) if isinstance(kind, str) else ()
            if kind == "reasoning":
                fields = fields if node.get("redacted") is True else ("signature",)
            for name in fields:
                value = node.get(name)
                if isinstance(value, str):
                    if value.startswith("attachment://"):
                        pointed.add(value.removeprefix("attachment://"))
                    node[name] = ""
            for value in node.values():
                blank(value)
        elif isinstance(node, list):
            for value in node:
                blank(value)

    blank(data)
    attachments = data.get("attachments") if isinstance(data, dict) else None
    if isinstance(attachments, dict):
        for key in pointed & set(attachments):
            attachments[key] = ""
    return json.dumps(data, ensure_ascii=False)


def check(where: str, text: str, rules: Sequence[Rule] = RULES) -> list[Finding]:
    findings: list[Finding] = []
    offset = 0
    for number, line in enumerate(text.split("\n"), start=1):
        for rule in rules:
            if rule.quick and not any(q.search(line) for q in rule.quick):
                continue
            for m in rule.pattern.finditer(line):
                if rule.allow and rule.allow(m.group(0), text, offset + m.start()):
                    continue
                findings.append(Finding(f"{where}:{number}", rule.name, m.group(0)))
        offset += len(line) + 1
    return findings


def term_rules(source: str) -> list[Rule]:
    """Rules from a file of one regular expression per line."""
    terms = (re.sub(r"(^|\s)#.*$", "", line).strip() for line in source.split("\n"))
    return [Rule("a private term", re.compile(term, re.I | re.A)) for term in terms if term]


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def _resolves(ref: str) -> bool:
    return subprocess.run(["git", "rev-parse", "--verify", "--quiet", ref], capture_output=True).returncode == 0


def commit_range(given: str) -> str:
    """`A..B`, or every commit reachable from B when A does not exist yet (a repository's first push)."""
    base, dots, tip = given.partition("..")
    if dots and base and not _resolves(base):
        return tip or "HEAD"
    return given


def main(argv: Sequence[str]) -> int:
    args = list(argv)

    def option(name: str) -> list[str]:
        return [args[i + 1] for i, arg in enumerate(args) if arg == name and i + 1 < len(args)]

    try:
        terms: list[Rule] = []
        for path in option("--terms"):
            with open(path, encoding="utf-8") as f:
                terms += term_rules(f.read())
    except (OSError, re.error) as error:
        print(f"cannot check: {error}", file=sys.stderr)
        return 2
    rules = [*RULES, *terms]
    findings: list[Finding] = []

    if "--no-files" not in args:
        for path in filter(None, _git("ls-files", "-z").split("\0")):
            if any(p.search(path) for p in NOT_OURS) or not os.path.isfile(path):
                continue
            if path.endswith(".eval"):
                try:
                    findings += check_log(path, LOG_RULES, terms)
                except (zipfile.BadZipFile, NotImplementedError, RuntimeError) as error:
                    print(f"cannot check {path}: {error}", file=sys.stderr)
                    return 2
                continue
            # The check's own patterns are left to the private terms alone.
            file_rules = terms if any(p.search(path) for p in THE_CHECK) else rules
            with open(path, "rb") as source:
                raw = source.read()
            if b"\0" in raw:
                continue
            findings += check(path, raw.decode("utf-8", errors="replace"), file_rules)
    for given in option("--commits"):
        log = _git("log", "--format=%H%x1f%an%x1f%B%x1e", commit_range(given))
        for entry in filter(None, (e.strip() for e in log.split("\x1e"))):
            sha, author, message = [*entry.split("\x1f"), "", ""][:3]
            if author == "dependabot[bot]":
                continue  # Its messages quote other projects' release notes.
            findings += check(f"commit {sha[:7]}", message, rules)
    for name in option("--text-env"):
        findings += check(name, os.environ.get(name, ""), rules)
    for path in option("--text-file"):
        with open(path, encoding="utf-8") as f:
            findings += check(path, f.read(), rules)

    for finding in findings:
        print(f"{finding.where}: {finding.rule}: {finding.match}", file=sys.stderr)
    print(f"{len(findings)} found." if findings else "Nothing found.", file=sys.stderr)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

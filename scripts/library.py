"""The Library: its entries checked, and its pages built from them.

    uv run python scripts/library.py check                  every file the Library holds
    uv run python scripts/library.py build                  the pages' includes, approved entries only
    uv run python scripts/library.py build --drafts         the same with drafts shown, for review
    uv run python scripts/library.py approve ID... [--on DATE]
    uv run python scripts/library.py drop ID... --note TEXT
    uv run python scripts/library.py pr-body                the text of a search's pull request

The entries are one CSL-JSON file, library/references.json, which pandoc also reads as a
bibliography. The bibliographic part of each entry is CSL's own, checked against CSL's schema
(library/schemas/csl-data.json); haau3's fields sit in CSL's `custom` object, which the schema keeps
for fields it does not define. No entry carries an abstract: a summary is written in our own words.

Every entry is either a draft (`"draft": true`), which no page lists, or approved: `approved` is the
day a person approved its summary, and `approved-summary` the SHA-256 of that summary. A summary
edited after its approval no longer matches, and the check fails until it is approved again.

Like scripts/note_numbers.py, `build` runs before `quarto render`, because Quarto reads a page's
included files before any pre-render script runs. The includes it writes are not committed.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import re
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LIBRARY = ROOT / "library"
REFERENCES = LIBRARY / "references.json"
SCREENED_OUT = LIBRARY / "screened-out.json"
SEARCHES = LIBRARY / "searches.json"
THEMES = LIBRARY / "themes.json"
AREAS = LIBRARY / "areas"
SCHEMA = LIBRARY / "schemas" / "csl-data.json"
CACHE = LIBRARY / ".cache"

SITE = "https://research.haau3.com"
MESH_SET = "https://id.nlm.nih.gov/mesh/"
MESH_BROWSER = "https://meshb.nlm.nih.gov/record/ui?ui="

# The kind of evidence an entry is. PubMed's publication-type names where one fits; ours for the rest.
EVIDENCE = (
    "survey",
    "qualitative study",
    "mixed methods",
    "randomized trial",
    "pilot or feasibility study",
    "observational study",
    "systematic review",
    "scoping review",
    "narrative review",
    "benchmark",
    "model evaluation",
    "system design and testing",
    "report",
)
READ = ("full", "abstract")
# Why a paper a search found is not in the Library.
REASONS = (
    "not about family caregivers of adults",
    "not about AI that converses",
    "not research: an editorial, commentary, letter, news item or announcement",
    "a conference abstract with no paper",
    "not in English",
    "published before the area's start",
    "the same work as another entry",
    "removed on review",
)
CUSTOM_REQUIRED = (
    "summary",
    "themes",
    "evidence",
    "studied",
    "peer-reviewed",
    "read",
    "headings",
    "checked",
    "found",
    "drafted-by",
)
CUSTOM_OPTIONAL = ("sample", "draft", "review-note", "approved", "approved-summary")
CUSTOM_ORDER = (*CUSTOM_REQUIRED[:5], "sample", *CUSTOM_REQUIRED[5:], "review-note", "draft", "approved", "approved-summary")

DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MESH_ID = re.compile(r"^D\d{6,9}$")
FOUND = re.compile(r"^(seed|search \d{4}-\d{2}-\d{2})$")
MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


# Reading and writing --------------------------------------------------------------------------------


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def dump(path: Path, data: Any) -> None:
    """Writes JSON the same way every time, so a change shows as a change in review."""
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def fingerprint(summary: str) -> str:
    return "sha256:" + hashlib.sha256(summary.encode("utf-8")).hexdigest()


def ordered(entry: dict[str, Any]) -> dict[str, Any]:
    """An entry with its custom fields in a fixed order, the CSL fields first as given."""
    custom = entry.get("custom", {})
    keys = [k for k in CUSTOM_ORDER if k in custom] + sorted(k for k in custom if k not in CUSTOM_ORDER)
    out = {k: v for k, v in entry.items() if k != "custom"}
    out["custom"] = {k: custom[k] for k in keys}
    return out


def save_references(entries: list[dict[str, Any]]) -> None:
    dump(REFERENCES, [ordered(e) for e in sorted(entries, key=lambda e: e["id"])])


def themes() -> list[dict[str, Any]]:
    return load(THEMES)["themes"]


def areas() -> list[dict[str, Any]]:
    return [load(p) for p in sorted(AREAS.glob("*.json"))]


def is_draft(entry: dict[str, Any]) -> bool:
    return entry.get("custom", {}).get("draft") is True


def is_approved(entry: dict[str, Any]) -> bool:
    custom = entry.get("custom", {})
    return "approved" in custom and custom.get("approved-summary") == fingerprint(custom.get("summary", ""))


def identifiers(record: dict[str, Any]) -> set[str]:
    """The keys a paper is known by, for finding it again: its DOI, PubMed id and arXiv id."""
    keys = set()
    doi = record.get("DOI") or record.get("doi")
    if doi:
        keys.add("doi:" + doi.lower())
    pmid = record.get("PMID") or record.get("pmid")
    if pmid:
        keys.add(f"pmid:{pmid}")
    arxiv = record.get("arxiv") or record.get("custom", {}).get("arxiv")
    number = record.get("number") if record.get("publisher") == "arXiv" else None
    for value in (arxiv, number):
        if value:
            keys.add("arxiv:" + re.sub(r"v\d+$", "", str(value)))
    return keys


# The check ------------------------------------------------------------------------------------------


def csl_validator():
    from jsonschema import Draft7Validator

    schema = load(SCHEMA)
    item = dict(schema["items"])
    item["definitions"] = schema["definitions"]
    return Draft7Validator(item)


def valid_date(text: Any) -> bool:
    if not isinstance(text, str) or not DATE.match(text):
        return False
    try:
        dt.date.fromisoformat(text)
    except ValueError:
        return False
    return True


def contains_key(value: Any, key: str) -> bool:
    if isinstance(value, dict):
        return key in value or any(contains_key(v, key) for v in value.values())
    if isinstance(value, list):
        return any(contains_key(v, key) for v in value)
    return False


def entry_problems(entry: dict[str, Any], theme_ids: set[str], validator=None) -> list[str]:
    """What is wrong with one entry, by name. An empty list is a good entry."""
    problems: list[str] = []
    if validator is not None:
        for error in sorted(validator.iter_errors(entry), key=lambda e: list(e.path)):
            where = "/".join(str(p) for p in error.path) or "the entry"
            problems.append(f"CSL schema at {where}: {error.message}")
    for field in ("id", "type", "title", "issued"):
        if not entry.get(field):
            problems.append(f"missing {field}")
    if not entry.get("author"):
        problems.append("missing author")
    if entry.get("issued") and not (entry["issued"].get("date-parts") or [[None]])[0][0]:
        problems.append("issued has no year")
    if not (entry.get("DOI") or entry.get("URL")):
        problems.append("missing DOI or URL")
    if contains_key(entry, "abstract"):
        problems.append("carries an abstract (summaries are in our own words)")

    custom = entry.get("custom")
    if not isinstance(custom, dict):
        return problems + ["missing custom (haau3's fields)"]
    for key in CUSTOM_REQUIRED:
        if key not in custom:
            problems.append(f"missing custom.{key}")
    for key in custom:
        if key not in CUSTOM_REQUIRED and key not in CUSTOM_OPTIONAL:
            problems.append(f"unknown field custom.{key}")

    if "summary" in custom and not (isinstance(custom["summary"], str) and custom["summary"].strip()):
        problems.append("custom.summary is empty")
    if "themes" in custom:
        given = custom["themes"]
        if not isinstance(given, list) or not given:
            problems.append("custom.themes names no theme")
        else:
            for theme in given:
                if theme not in theme_ids:
                    problems.append(f"custom.themes: unknown theme {theme!r}")
            if len(set(given)) != len(given):
                problems.append("custom.themes repeats a theme")
    if "evidence" in custom and custom["evidence"] not in EVIDENCE:
        problems.append(f"custom.evidence: {custom['evidence']!r} is not a kind of evidence the Library uses")
    if "studied" in custom and not (isinstance(custom["studied"], str) and custom["studied"].strip()):
        problems.append("custom.studied is empty")
    if "peer-reviewed" in custom and not isinstance(custom["peer-reviewed"], bool):
        problems.append("custom.peer-reviewed is not true or false")
    if "read" in custom and custom["read"] not in READ:
        problems.append("custom.read is not 'full' or 'abstract'")
    if "headings" in custom:
        headings = custom["headings"]
        if not isinstance(headings, list):
            problems.append("custom.headings is not a list")
        else:
            for heading in headings:
                if not (
                    isinstance(heading, dict)
                    and MESH_ID.match(str(heading.get("id", "")))
                    and isinstance(heading.get("name"), str)
                    and isinstance(heading.get("major"), bool)
                ):
                    problems.append(f"custom.headings: {heading!r} is not an NLM heading with id, name and major")
    if "checked" in custom and not valid_date(custom["checked"]):
        problems.append("custom.checked is not a date (YYYY-MM-DD)")
    if "found" in custom and not (isinstance(custom["found"], str) and FOUND.match(custom["found"])):
        problems.append("custom.found is neither 'seed' nor 'search YYYY-MM-DD'")
    if "drafted-by" in custom and not (isinstance(custom["drafted-by"], str) and custom["drafted-by"]):
        problems.append("custom.drafted-by is empty")
    if "sample" in custom:
        sample = custom["sample"]
        if not (
            isinstance(sample, dict)
            and set(sample) == {"n", "of"}
            and isinstance(sample["n"], int)
            and not isinstance(sample["n"], bool)
            and sample["n"] > 0
            and isinstance(sample["of"], str)
            and sample["of"]
        ):
            problems.append("custom.sample is not {n: a whole number, of: what was counted}")

    draft = custom.get("draft")
    approved = "approved" in custom or "approved-summary" in custom
    if draft is not None and draft is not True:
        problems.append("custom.draft is only ever true")
    if draft is True and approved:
        problems.append("is both a draft and approved")
    elif draft is None and not approved:
        problems.append("is neither a draft nor approved")
    elif approved:
        if not valid_date(custom.get("approved")):
            problems.append("custom.approved is not a date (YYYY-MM-DD)")
        if custom.get("approved-summary") != fingerprint(custom.get("summary", "")):
            problems.append("its summary has changed since it was approved")
        if "review-note" in custom:
            problems.append("an approved entry keeps no review note")
    return problems


def check_entries(entries: list[dict[str, Any]], theme_ids: set[str], validator=None) -> list[str]:
    problems: list[str] = []
    if not isinstance(entries, list):
        return ["references.json is not a list"]
    seen: dict[str, str] = {}
    for index, entry in enumerate(entries):
        name = entry.get("id") or f"entry {index + 1}"
        problems += [f"{name}: {p}" for p in entry_problems(entry, theme_ids, validator)]
        for key in identifiers(entry) | {f"id:{entry.get('id')}"}:
            if key in seen:
                problems.append(f"{name}: {key} is also {seen[key]}")
            seen[key] = name
    return problems


def check_screened_out(rows: list[dict[str, Any]]) -> list[str]:
    problems: list[str] = []
    for index, row in enumerate(rows):
        name = row.get("title", f"row {index + 1}")[:60]
        if not identifiers(row) and not row.get("url"):
            problems.append(f"screened out {name!r}: no DOI, PubMed id, arXiv id or URL")
        if not row.get("title"):
            problems.append(f"screened out row {index + 1}: no title")
        if row.get("reason") not in REASONS:
            problems.append(f"screened out {name!r}: {row.get('reason')!r} is not a listed reason")
        if not valid_date(row.get("search")):
            problems.append(f"screened out {name!r}: search is not a date")
    return problems


def check_themes(given: list[dict[str, Any]], area_ids: set[str]) -> list[str]:
    problems: list[str] = []
    ids = [t.get("id") for t in given]
    if len(set(ids)) != len(ids):
        problems.append("themes: an id is repeated")
    for theme in given:
        for field in ("id", "name", "definition"):
            if not theme.get(field):
                problems.append(f"theme {theme.get('id')}: missing {field}")
        for heading in theme.get("headings", []):
            if not MESH_ID.match(str(heading.get("id", ""))) or not heading.get("name"):
                problems.append(f"theme {theme.get('id')}: {heading!r} is not an NLM heading")
        if theme.get("area") is not None and theme["area"] not in area_ids:
            problems.append(f"theme {theme.get('id')}: area {theme['area']!r} has no file in library/areas")
    return problems


def check_areas(given: list[dict[str, Any]], theme_ids: set[str]) -> list[str]:
    problems: list[str] = []
    for area in given:
        name = area.get("id", "an area")
        for field in ("id", "theme", "title", "from", "include", "exclude", "search"):
            if not area.get(field):
                problems.append(f"area {name}: missing {field}")
        if area.get("theme") not in theme_ids:
            problems.append(f"area {name}: unknown theme {area.get('theme')!r}")
        if not valid_date(area.get("from")):
            problems.append(f"area {name}: from is not a date")
        if not (LIBRARY / name / "index.qmd").exists():
            problems.append(f"area {name}: no page at library/{name}/index.qmd")
    return problems


def check_all() -> list[str]:
    given_themes = themes()
    given_areas = areas()
    theme_ids = {t["id"] for t in given_themes}
    problems = check_themes(given_themes, {a.get("id") for a in given_areas})
    problems += check_areas(given_areas, theme_ids)
    problems += check_entries(load(REFERENCES), theme_ids, csl_validator())
    problems += check_screened_out(load(SCREENED_OUT))
    problems += check_searches(load(SEARCHES))
    return problems


def check_searches(searches: list[dict[str, Any]]) -> list[str]:
    """Every search has a date, and an area is searched at most once a day: a search is named by its
    date, which its entries and the papers it set aside carry."""
    if not isinstance(searches, list) or any(not valid_date(s.get("date")) for s in searches):
        return ["searches.json: every search needs a date"]
    seen, problems = set(), []
    for search in searches:
        key = (search.get("area"), search["date"])
        if key in seen:
            problems.append(f"searches.json: {key[0]} is searched twice on {key[1]}")
        seen.add(key)
    return problems


# Showing an entry -----------------------------------------------------------------------------------


def e(text: Any) -> str:
    return html.escape(str(text), quote=True)


def date_parts(entry: dict[str, Any]) -> tuple[int, int, int]:
    parts = (entry.get("issued", {}).get("date-parts") or [[0]])[0]
    parts = [int(p) for p in parts] + [0, 0, 0]
    return parts[0], parts[1], parts[2]


def long_date(year: int, month: int = 0, day: int = 0) -> str:
    if month and day:
        return f"{MONTHS[month - 1]} {day}, {year}"
    if month:
        return f"{MONTHS[month - 1]} {year}"
    return str(year)


def iso_long(text: str) -> str:
    day = dt.date.fromisoformat(text)
    return long_date(day.year, day.month, day.day)


def person(name: dict[str, Any]) -> str:
    if name.get("literal"):
        return name["literal"]
    return " ".join(p for p in (name.get("given"), name.get("family")) if p)


def authors(entry: dict[str, Any], shown: int = 3) -> str:
    """The first few authors by name: "A, B and C", or "A, B, C and 4 others"."""
    names = [person(a) for a in entry.get("author", [])]
    if len(names) > shown:
        rest = len(names) - shown
        return ", ".join(names[:shown]) + f" and {rest} other{'s' if rest > 1 else ''}"
    if len(names) > 1:
        return ", ".join(names[:-1]) + " and " + names[-1]
    return names[0] if names else ""


def link(entry: dict[str, Any]) -> str:
    return f"https://doi.org/{entry['DOI']}" if entry.get("DOI") else entry["URL"]


def venue(entry: dict[str, Any]) -> str:
    if entry.get("container-title"):
        return entry["container-title"]
    if entry.get("publisher"):
        return entry["publisher"]
    return ""


def sentence_case(text: str) -> str:
    return text[:1].upper() + text[1:]


def entry_html(entry: dict[str, Any], theme_names: dict[str, str], draft_number: int | None = None) -> str:
    custom = entry["custom"]
    year, month, day = date_parts(entry)
    where = venue(entry)
    byline = ", ".join(p for p in (e(authors(entry)), f"<em>{e(where)}</em>" if where else "", e(long_date(year, month, day))) if p)

    if custom.get("peer-reviewed"):
        reviewed = "Peer reviewed"
    elif entry.get("type") == "article":
        reviewed = "Not peer reviewed (a preprint)"
    else:
        reviewed = "Not peer reviewed"
    studied = e(custom["studied"])
    if custom.get("sample"):
        studied += f" ({custom['sample']['n']:,} {e(custom['sample']['of'])})"
    read = "The full paper" if custom["read"] == "full" else "The abstract only"
    theme_links = ", ".join(
        f'<a href="../#theme-{e(t)}">{e(theme_names.get(t, t))}</a>' for t in custom["themes"]
    )
    if custom["headings"]:
        headings = ", ".join(
            f'<a href="{MESH_BROWSER}{e(h["id"])}">{e(h["name"])}</a>' + (" (major topic)" if h["major"] else "")
            for h in custom["headings"]
        )
    else:
        headings = "None: not indexed in PubMed"

    lines = [f'<article class="library-entry" id="entry-{e(entry["id"])}" aria-labelledby="title-{e(entry["id"])}">']
    if draft_number is not None:
        note = f" {e(custom['review-note'])}" if custom.get("review-note") else ""
        lines.append(f'<p class="library-draft"><strong>Draft {draft_number}, for review.</strong>{note}</p>')
    lines += [
        f'<h3 id="title-{e(entry["id"])}"><a href="{e(link(entry))}">{e(entry["title"])}</a></h3>',
        f'<p class="library-byline">{byline}</p>',
        f'<p class="library-summary">{e(custom["summary"])}</p>',
        '<dl class="library-facts">',
        f"<dt>Kind of evidence</dt><dd>{e(sentence_case(custom['evidence']))}. {reviewed}.</dd>",
        f"<dt>Who was studied</dt><dd>{studied}</dd>",
        f"<dt>What we read</dt><dd>{read}</dd>",
        f"<dt>Themes</dt><dd>{theme_links}</dd>",
        f"<dt>Subject headings</dt><dd>{headings}</dd>",
        f"<dt>Last checked</dt><dd>{e(iso_long(custom['checked']))}</dd>",
        "</dl>",
        "</article>",
    ]
    return "\n".join(lines)


def scholarly(entry: dict[str, Any], theme_names: dict[str, str]) -> dict[str, Any]:
    """schema.org's description of the work itself: its facts and headings, never our summary."""
    year, month, day = date_parts(entry)
    published = f"{year:04d}" + (f"-{month:02d}" if month else "") + (f"-{day:02d}" if month and day else "")
    node: dict[str, Any] = {
        "@type": "Report" if entry.get("type") == "report" else "ScholarlyArticle",
        "name": entry["title"],
        "url": link(entry),
        "datePublished": published,
        "inLanguage": "en",
        "author": [
            {"@type": "Organization", "name": a["literal"]} if a.get("literal") else {"@type": "Person", "name": person(a)}
            for a in entry.get("author", [])
        ],
    }
    ids = []
    if entry.get("DOI"):
        ids.append({"@type": "PropertyValue", "propertyID": "DOI", "value": entry["DOI"]})
        node["sameAs"] = f"https://doi.org/{entry['DOI']}"
    if entry.get("PMID"):
        ids.append({"@type": "PropertyValue", "propertyID": "PMID", "value": entry["PMID"]})
    if ids:
        node["identifier"] = ids
    if entry.get("container-title"):
        node["isPartOf"] = {"@type": "Periodical", "name": entry["container-title"]}
    elif entry.get("publisher"):
        node["publisher"] = {"@type": "Organization", "name": entry["publisher"]}
    custom = entry["custom"]
    node["about"] = [
        {"@type": "DefinedTerm", "name": h["name"], "termCode": h["id"], "inDefinedTermSet": MESH_SET}
        for h in custom["headings"]
    ] + [
        {"@type": "DefinedTerm", "name": theme_names.get(t, t), "termCode": t, "inDefinedTermSet": f"{SITE}/library/#themes"}
        for t in custom["themes"]
    ]
    return node


def json_ld(area: dict[str, Any], listed: list[dict[str, Any]], theme_names: dict[str, str]) -> str:
    page = {
        "@context": "https://schema.org",
        "@type": "CollectionPage",
        "name": area["title"],
        "url": f"{SITE}/library/{area['id']}/",
        "isPartOf": {"@type": "WebSite", "name": "haau3 Research", "url": SITE},
        "mainEntity": {
            "@type": "ItemList",
            "numberOfItems": len(listed),
            "itemListElement": [
                {"@type": "ListItem", "position": i + 1, "item": scholarly(entry, theme_names)}
                for i, entry in enumerate(listed)
            ],
        },
    }
    text = json.dumps(page, indent=2, ensure_ascii=False).replace("</", "<\\/")
    return f'<script type="application/ld+json">\n{text}\n</script>'


def newest_first(entries: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(entries, key=lambda x: (date_parts(x), x["title"].lower()), reverse=True)


# Building the pages ---------------------------------------------------------------------------------


def raw(html_text: str) -> str:
    return "```{=html}\n" + html_text + "\n```\n"


def area_entries(area: dict[str, Any], entries: list[dict[str, Any]], drafts: bool) -> tuple[list, list]:
    mine = [x for x in entries if area["theme"] in x["custom"]["themes"]]
    listed = newest_first(x for x in mine if is_approved(x))
    shown_drafts = newest_first(x for x in mine if is_draft(x)) if drafts else []
    return listed, shown_drafts


def latest_search(area_id: str, searches: list[dict[str, Any]]) -> dict[str, Any] | None:
    mine = [s for s in searches if s.get("area") == area_id]
    return max(mine, key=lambda s: s["date"]) if mine else None


def area_searches(area: dict[str, Any], searches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted((s for s in searches if s.get("area") == area["id"]), key=lambda s: s["date"], reverse=True)


def search_section(area: dict[str, Any], searches: list[dict[str, Any]], listed: list[dict[str, Any]]) -> str:
    mine = area_searches(area, searches)
    if not mine:
        return "This area has not been searched yet.\n"
    last = mine[0]
    seeds = sum(1 for x in listed if x["custom"]["found"] == "seed")
    out = [
        f"**Last searched {iso_long(last['date'])}.** A search runs on the first day of each month and "
        "looks for what PubMed and arXiv added since the one before. A model screens every paper it finds "
        "against the scope above and drafts an entry for each one that fits, and a person approves every "
        "entry before it is listed here. A month whose search was not published shows as a gap in the "
        "table below.\n",
        "The searches, exactly as run:\n",
    ]
    for database in last.get("databases", []):
        out.append(f"- **{database['name']}:** `{database['query']}`")
    out.append("")
    out.append(
        f"Besides the searches, {seeds} {'entry was' if seeds == 1 else 'entries were'} seeded from the "
        "reading this area started from. Every search's record, with the model that drafted its entries "
        "and the tokens it used, is in [the repository](https://github.com/haau3/research/tree/main/library)."
    )
    return "\n".join(out) + "\n"


def searches_table(area: dict[str, Any], searches: list[dict[str, Any]], listed: list[dict[str, Any]]) -> str:
    mine = area_searches(area, searches)
    if not mine:
        return ""
    out = [
        "| Searched | For papers added | Found | Screened out | Drafted | Listed now | Drafting cost |",
        "|:--|:--|--:|--:|--:|--:|--:|",
    ]
    for search in mine:
        listed_now = sum(1 for x in listed if x["custom"]["found"] == f"search {search['date']}")
        window = f"{iso_long(search['window']['from'])} to {iso_long(search['window']['to'])}"
        found = sum(d.get("found", 0) for d in search.get("databases", []))
        cost = f"${search['cost-usd']:.2f}" if "cost-usd" in search else "none"
        out.append(
            f"| {iso_long(search['date'])} | {window} | {found:,} | {search.get('screened-out', 0):,} | "
            f"{search.get('drafted', 0):,} | {listed_now:,} | {cost} |"
        )
    out += [
        "",
        ": Every search of this area. Found counts what each query returned before screening, so a paper "
        "in both databases counts twice. Listed now counts the entries from that search a person has "
        "approved. {#tbl-searches}",
        "",
    ]
    return "\n".join(out)


def scope_section(area: dict[str, Any]) -> str:
    out = ["**In this area:**\n"] + [f"- {line}" for line in area["include"]]
    out += ["", "**Not in this area:**\n"] + [f"- {line}" for line in area["exclude"]]
    return "\n".join(out) + "\n"


def entries_section(listed: list, shown_drafts: list, theme_names: dict[str, str], area: dict[str, Any]) -> str:
    parts = []
    if shown_drafts:
        parts.append(
            raw(
                f'<p class="library-draft"><strong>{len(shown_drafts)} drafts for review</strong> come first, '
                "numbered. This view is never published.</p>"
            )
        )
        parts += [raw(entry_html(x, theme_names, i + 1)) for i, x in enumerate(shown_drafts)]
    if not listed and not shown_drafts:
        parts.append("No entry has been approved yet.\n")
    parts += [raw(entry_html(x, theme_names)) for x in listed]
    parts.append(raw(json_ld(area, listed, theme_names)))
    return "\n".join(parts)


def themes_section(given: list[dict[str, Any]]) -> str:
    out = []
    for theme in given:
        lines = [
            f'<section class="library-theme" id="theme-{e(theme["id"])}" aria-labelledby="theme-name-{e(theme["id"])}">',
            f'<h3 id="theme-name-{e(theme["id"])}">{e(theme["name"])}</h3>',
            f"<p>{e(theme['definition'])}</p>",
        ]
        if theme["headings"]:
            headings = ", ".join(
                f'<a href="{MESH_BROWSER}{e(h["id"])}">{e(h["name"])}</a> ({e(h["id"])})' for h in theme["headings"]
            )
            lines.append(f"<p>NLM's subject headings: {headings}.</p>")
        else:
            lines.append("<p>NLM has no subject heading for this theme.</p>")
        if theme.get("area"):
            lines.append(f'<p><a href="{e(theme["area"])}/">The area for this theme</a>.</p>')
        lines.append("</section>")
        out.append(raw("\n".join(lines)))
    return "\n".join(out)


def areas_section(given_areas: list, entries: list, searches: list) -> str:
    out = []
    for area in given_areas:
        listed, _ = area_entries(area, entries, drafts=False)
        last = latest_search(area["id"], searches)
        when = f", last searched {iso_long(last['date'])}" if last else ""
        out.append(f"- [{area['title']}]({area['id']}/index.qmd): {len(listed)} entries{when}.")
    return "\n".join(out) + "\n"


def build(drafts: bool = False) -> list[Path]:
    given_themes = themes()
    theme_names = {t["id"]: t["name"] for t in given_themes}
    given_areas = areas()
    entries = load(REFERENCES)
    searches = load(SEARCHES)
    written = []

    def write(path: Path, text: str) -> None:
        path.write_text(text, encoding="utf-8")
        written.append(path)

    write(LIBRARY / "_themes.md", themes_section(given_themes))
    write(LIBRARY / "_areas.md", areas_section(given_areas, entries, searches))
    for area in given_areas:
        listed, shown_drafts = area_entries(area, entries, drafts)
        folder = LIBRARY / area["id"]
        write(folder / "_scope.md", scope_section(area))
        write(folder / "_search.md", search_section(area, searches, listed))
        write(folder / "_searches-table.md", searches_table(area, searches, listed))
        write(folder / "_entries.md", entries_section(listed, shown_drafts, theme_names, area))
        write(folder / "_count.md", f"{len(listed)} {'entry' if len(listed) == 1 else 'entries'}, newest first.\n")
        write(folder / "_limits.md", limits_section(area))
        # JSON is YAML, and Quarto reads it as the page's metadata: the title is stated once, in the area.
        write(folder / "_area.yml", json.dumps({"title": area["title"]}, ensure_ascii=False) + "\n")
    return written


def limits_section(area: dict[str, Any]) -> str:
    return (
        f"The searches cover two databases, PubMed and arXiv, in English, from {iso_long(area['from'])}. "
        "Work in neither, such as many conference papers and reports, is here only when it was part of the "
        "reading this area started from or a reader points to it. A model screens what the searches find, so "
        "a paper it wrongly set aside is missing; each one it set aside is listed with its reason in "
        "[the repository](https://github.com/haau3/research/blob/main/library/screened-out.json). Each "
        "summary says what the work reports. Where only the abstract could be read, the entry says so.\n"
    )


# Approving and dropping -----------------------------------------------------------------------------


def approve(ids: list[str], on: str) -> list[str]:
    """Marks drafts approved on the reviewer's word. Returns what it could not do."""
    if not valid_date(on):
        return [f"{on} is not a date"]
    entries = load(REFERENCES)
    by_id = {x["id"]: x for x in entries}
    problems = []
    for entry_id in ids:
        entry = by_id.get(entry_id)
        if entry is None:
            problems.append(f"{entry_id}: no such entry")
            continue
        custom = entry["custom"]
        if not is_draft(entry):
            problems.append(f"{entry_id}: not a draft")
            continue
        custom.pop("draft")
        custom.pop("review-note", None)
        custom["approved"] = on
        custom["approved-summary"] = fingerprint(custom["summary"])
    if not problems:
        save_references(entries)
    return problems


def drop(ids: list[str], note: str, on: str, reason: str = "removed on review") -> list[str]:
    """Moves entries, drafts or approved, to the screened-out list on a reviewer's word, with the
    reason, so the search never offers them again."""
    if reason not in REASONS:
        return [f"{reason!r} is not a listed reason"]
    entries = load(REFERENCES)
    rows = load(SCREENED_OUT)
    by_id = {x["id"]: x for x in entries}
    problems = []
    for entry_id in ids:
        entry = by_id.get(entry_id)
        if entry is None:
            problems.append(f"{entry_id}: no such entry")
            continue
        row = {"title": entry["title"]}
        for field, key in (("DOI", "doi"), ("PMID", "pmid")):
            if entry.get(field):
                row[key] = entry[field]
        if entry.get("publisher") == "arXiv" and entry.get("number"):
            row["arxiv"] = entry["number"]
        if not identifiers(row):
            row["url"] = entry.get("URL")
        row.update({"reason": reason, "note": note, "search": on})
        rows.append(row)
        entries.remove(entry)
    if not problems:
        save_references(entries)
        dump(SCREENED_OUT, rows)
    return problems


# The pull request's text ----------------------------------------------------------------------------


# GitHub refuses a pull request description over 65,536 characters; this leaves room for its own text.
PR_BODY_LIMIT = 60_000


def pr_body(limit: int = PR_BODY_LIMIT) -> str:
    """The pull request's text, in full when it fits. When it would not, the papers set aside are
    counted rather than listed, then each draft is named rather than written out, and the text says
    where the rest is: the files the pull request changes."""
    for level in ("full", "no-screened-list", "draft-titles"):
        text = _pr_body(level)
        if len(text) <= limit:
            return text
    return text[: limit - 200] + "\n\n…cut short to fit: read the drafts in `library/references.json`.\n"


def _pr_body(level: str) -> str:
    entries = load(REFERENCES)
    searches = load(SEARCHES)
    theme_names = {t["id"]: t["name"] for t in themes()}
    out = []
    for area in areas():
        last = latest_search(area["id"], searches)
        if not last:
            continue
        found = sum(d.get("found", 0) for d in last.get("databases", []))
        out.append(f"## {area['title']}\n")
        out.append(
            f"Searched {iso_long(last['date'])}, for papers added from {iso_long(last['window']['from'])} "
            f"to {iso_long(last['window']['to'])}: {found:,} found, {last.get('screened-out', 0):,} screened "
            f"out, {last.get('drafted', 0):,} drafted. Drafting cost ${last.get('cost-usd', 0):.2f}.\n"
        )
        drafts = newest_first(x for x in entries if is_draft(x) and area["theme"] in x["custom"]["themes"])
        out.append(f"### Drafts to approve ({len(drafts)})\n")
        if not drafts:
            out.append("None this time.\n")
        if drafts and level == "draft-titles":
            out.append("Too many to write out here: each is in `library/references.json`, and as pages with "
                       "`scripts/library.py build --drafts`.\n")
            out += [f"{number}. [{entry['title']}]({link(entry)}) `{entry['id']}`" for number, entry in enumerate(drafts, 1)]
            out.append("")
            drafts = []
        for number, entry in enumerate(drafts, 1):
            custom = entry["custom"]
            year, month, day = date_parts(entry)
            sample = f" ({custom['sample']['n']:,} {custom['sample']['of']})" if custom.get("sample") else ""
            out.append(f"**{number}. [{entry['title']}]({link(entry)})** `{entry['id']}`  ")
            out.append(f"{authors(entry)}, {venue(entry)}, {long_date(year, month, day)}  ")
            out.append(
                f"{sentence_case(custom['evidence'])}; {'peer reviewed' if custom['peer-reviewed'] else 'not peer reviewed'}; "
                f"read: {custom['read']}; studied: {custom['studied']}{sample}; themes: "
                f"{', '.join(theme_names.get(t, t) for t in custom['themes'])}"
            )
            if custom.get("review-note"):
                out.append(f"\n> {custom['review-note']}")
            out.append(f"\n{custom['summary']}\n")
        rows = [r for r in load(SCREENED_OUT) if r.get("search") == last["date"]]
        out.append(f"### Screened out ({len(rows)})\n")
        if level == "full":
            out.append("<details><summary>Each paper the model screened out, with its reason</summary>\n")
            for row in rows:
                out.append(f"- {row['title']}: {row['reason']}")
            out.append("\n</details>\n")
        elif rows:
            out.append("Too many to list here: each, with its reason, is in `library/screened-out.json`.\n")
        recheck = last.get("recheck", {})
        findings = recheck.get("findings", [])
        out.append(f"### Re-check of published entries ({recheck.get('entries', 0)} checked)\n")
        out.append("\n".join(f"- `{f['id']}`: {f['finding']}" for f in findings) if findings else "Nothing changed.")
        out.append("")
    return "\n".join(out) + "\n"


# The command line -----------------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check")
    build_cmd = commands.add_parser("build")
    build_cmd.add_argument("--drafts", action="store_true", help="show drafts, for review on a laptop")
    approve_cmd = commands.add_parser("approve")
    approve_cmd.add_argument("ids", nargs="+")
    approve_cmd.add_argument("--on", default=dt.datetime.now(dt.timezone.utc).date().isoformat())
    drop_cmd = commands.add_parser("drop")
    drop_cmd.add_argument("ids", nargs="+")
    drop_cmd.add_argument("--note", required=True)
    drop_cmd.add_argument("--reason", default="removed on review", choices=REASONS)
    drop_cmd.add_argument("--on", default=dt.datetime.now(dt.timezone.utc).date().isoformat())
    commands.add_parser("pr-body")
    args = parser.parse_args(argv)

    if args.command == "check":
        problems = check_all()
        for problem in problems:
            print(problem, file=sys.stderr)
        if not problems:
            entries = load(REFERENCES)
            approved = sum(1 for x in entries if is_approved(x))
            print(f"library: {approved} approved, {len(entries) - approved} drafts, every file valid", file=sys.stderr)
        return 1 if problems else 0
    if args.command == "build":
        problems = check_all()
        if problems:
            print("library: not built, the check fails (scripts/library.py check)", file=sys.stderr)
            return 1
        for path in build(drafts=args.drafts):
            print(f"wrote {path.relative_to(ROOT)}", file=sys.stderr)
        return 0
    if args.command in ("approve", "drop"):
        problems = approve(args.ids, args.on) if args.command == "approve" else drop(args.ids, args.note, args.on, args.reason)
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1 if problems else 0
    if args.command == "pr-body":
        sys.stdout.write(pr_body())
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

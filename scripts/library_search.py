"""Searches PubMed and arXiv for the papers an area may hold, and re-checks the entries it holds.

    uv run python scripts/library_search.py search --area caregiving-and-ai [--since DATE] [--until DATE]
    uv run python scripts/library_search.py seed --area caregiving-and-ai ID...
    uv run python scripts/library_search.py recheck

`search` runs the area's two queries (library/areas/<area>.json) over the papers each database
added between two dates: PubMed by the date a record entered it, arXiv by the date a paper was
submitted. By default it starts a week before the area's last search, so a paper indexed late is
not missed, and ends today. It drops every paper the Library already holds or has screened out, by
DOI, PubMed id and arXiv id, and writes the rest to library/.cache/<area>/ for scripts/library_draft.py
to screen. `seed` does the same for papers named by DOI, PubMed id (pmid:N) or arXiv id (arxiv:N).

Each paper's bibliographic facts are CSL: NCBI's own export for a PubMed record, and the DOI's
registration agency's for the rest (DataCite for arXiv), cut to the fields CSL's schema defines.
Abstracts and full texts go to the cache only: they are others' words, read to write a summary, and
never committed (.gitignore).

`recheck` looks at every entry: its DOI still resolves, PubMed has not marked it retracted, and an
arXiv preprint has or has not been published since. It sets `checked` to today on every entry it
could check, and writes what changed to the cache for the pull request.

NCBI asks programs that call its E-utilities to name themselves and give a contact, so every call
names research.haau3.com as its tool and gives the contact this repository publishes. Calls are
spaced as NCBI (three a second) and arXiv (one every three seconds) ask.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import library  # noqa: E402

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
CTXP = "https://api.ncbi.nlm.nih.gov/lit/ctxp/v1/pubmed/"
ARXIV_API = "https://export.arxiv.org/api/query"
NCBI_ID = {"tool": "research.haau3.com", "email": "hello@haau3.com"}
USER_AGENT = "research.haau3.com/1.0 (+https://github.com/haau3/research)"
ATOM = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
# The CSL fields an entry keeps from a source's export. Others are dropped, the abstract above all.
CSL_KEPT = (
    "type", "title", "author", "issued", "container-title", "container-title-short", "volume", "issue",
    "page", "DOI", "PMID", "PMCID", "ISSN", "URL", "publisher", "number", "genre",
)
# A text shorter than this is a front page, not a paper.
MIN_FULL_TEXT = 3000
# The most of a paper sent to the model: about 60,000 tokens, more than a long paper's body.
MAX_TEXT = 240_000

_last_call: dict[str, float] = {}


def wait_turn(host: str, gap: float) -> None:
    since = time.monotonic() - _last_call.get(host, 0.0)
    if since < gap:
        time.sleep(gap - since)
    _last_call[host] = time.monotonic()


def fetch(url: str, *, data: dict[str, str] | None = None, accept: str | None = None, gap: float = 0.4) -> bytes:
    """GET (or POST with data) with a few retries on a busy server. Raises on anything else."""
    host = urllib.parse.urlsplit(url).netloc
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    headers = {"User-Agent": USER_AGENT}
    if accept:
        headers["Accept"] = accept
    for attempt in range(4):
        wait_turn(host, gap)
        request = urllib.request.Request(url, data=body, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            if error.code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(5 * (attempt + 1))
                continue
            raise
        except urllib.error.URLError:
            if attempt < 3:
                time.sleep(5 * (attempt + 1))
                continue
            raise
    raise RuntimeError("unreachable")


def ncbi(path: str, params: dict[str, str]) -> bytes:
    return fetch(EUTILS + path, data={**params, **NCBI_ID}, gap=0.4)


def text_of(element: ET.Element | None) -> str:
    return " ".join("".join(element.itertext()).split()) if element is not None else ""


# CSL ------------------------------------------------------------------------------------------------


def clean_csl(record: dict[str, Any]) -> dict[str, Any]:
    """A source's CSL cut to the fields an entry keeps, in a form CSL's schema accepts."""
    out: dict[str, Any] = {}
    for key in CSL_KEPT:
        value = record.get(key)
        if value in (None, "", []):
            continue
        if key == "author":
            value = [
                {k: v for k, v in name.items() if k in ("family", "given", "literal", "suffix") and v}
                for name in value
                if isinstance(name, dict)
            ]
            value = [name for name in value if name]
        if key == "issued":
            parts = value.get("date-parts") if isinstance(value, dict) else None
            if not parts or not parts[0] or parts[0][0] in (None, ""):
                continue
            value = {"date-parts": [[int(p) for p in parts[0][:3]]]}
        if isinstance(value, (int, float)) and key not in ("issued",):
            value = str(value)
        out[key] = value
    # Crossref writes its own type names; CSL's are these.
    out["type"] = {"journal-article": "article-journal", "posted-content": "article", "proceedings-article": "paper-conference", "book-chapter": "chapter"}.get(
        out.get("type", ""), out.get("type", "article-journal")
    )
    if "title" in out:
        out["title"] = " ".join(html.unescape(re.sub(r"<[^>]+>", "", out["title"])).split())
    return out


def entry_id(csl: dict[str, Any], taken: set[str]) -> str:
    """A citation key a person can read: first author's family name, year and first real word."""
    first = (csl.get("author") or [{}])[0]
    name = first.get("family") or first.get("literal") or "anon"
    name = re.sub(r"[^a-z]", "", name.lower().split()[0] if name.split() else "anon") or "anon"
    year = str(library.date_parts(csl)[0] or "nd")
    stop = {"a", "an", "the", "of", "and", "in", "on", "for", "to", "with", "how", "what", "when", "do", "does", "can", "is", "are"}
    words = [w for w in re.findall(r"[a-z0-9]+", csl.get("title", "").lower()) if w not in stop]
    base = f"{name}{year}{words[0] if words else ''}"
    key, suffix = base, ord("a")
    while key in taken:
        key = f"{base}{chr(suffix)}"
        suffix += 1
    return key


# PubMed ---------------------------------------------------------------------------------------------


def pubmed_search(query: str, since: dt.date, until: dt.date) -> list[str]:
    term = f"({query}) AND {since:%Y/%m/%d}:{until:%Y/%m/%d}[edat]"
    ids: list[str] = []
    while True:
        found = json.loads(ncbi("esearch.fcgi", {"db": "pubmed", "term": term, "retmode": "json", "retmax": "9999", "retstart": str(len(ids))}))
        batch = found["esearchresult"]["idlist"]
        ids += batch
        if not batch or len(ids) >= int(found["esearchresult"]["count"]):
            return ids


def pubmed_records(pmids: list[str]) -> list[dict[str, Any]]:
    """What PubMed says about each paper: title, journal, date, language, publication types, MeSH
    headings (with NLM's ids and major-topic flags), DOI, PMCID, retraction and abstract."""
    records = []
    for start in range(0, len(pmids), 200):
        root = ET.fromstring(ncbi("efetch.fcgi", {"db": "pubmed", "retmode": "xml", "id": ",".join(pmids[start : start + 200])}))
        for article in root.iter("PubmedArticle"):
            citation = article.find("MedlineCitation")
            pmid = citation.findtext("PMID")
            art = citation.find("Article")
            headings = []
            for heading in citation.findall("MeshHeadingList/MeshHeading"):
                name = heading.find("DescriptorName")
                major = name.get("MajorTopicYN") == "Y" or any(q.get("MajorTopicYN") == "Y" for q in heading.findall("QualifierName"))
                headings.append({"id": name.get("UI"), "name": text_of(name), "major": major})
            ids = {i.get("IdType"): (i.text or "").strip() for i in article.findall("PubmedData/ArticleIdList/ArticleId")}
            abstract = " ".join(
                (f"{part.get('Label')}: " if part.get("Label") else "") + text_of(part) for part in art.findall("Abstract/AbstractText")
            )
            pubtypes = [text_of(p) for p in art.findall("PublicationTypeList/PublicationType")]
            retracted = "Retracted Publication" in pubtypes or any(
                c.get("RefType") == "RetractionIn" for c in citation.findall("CommentsCorrectionsList/CommentsCorrections")
            )
            records.append(
                {
                    "pmid": pmid,
                    "title": text_of(art.find("ArticleTitle")),
                    "journal": text_of(art.find("Journal/Title")),
                    "language": [text_of(x) for x in art.findall("Language")],
                    "pubtypes": pubtypes,
                    "headings": headings,
                    "doi": ids.get("doi"),
                    "pmcid": ids.get("pmc"),
                    "retracted": retracted,
                    "abstract": abstract,
                }
            )
    return records


def pubmed_csl(pmids: list[str]) -> dict[str, dict[str, Any]]:
    out = {}
    for start in range(0, len(pmids), 50):
        chunk = pmids[start : start + 50]
        query = "&".join(f"id={p}" for p in chunk)
        data = json.loads(fetch(f"{CTXP}?format=csl&{query}", gap=0.4))
        for record in data if isinstance(data, list) else [data]:
            out[str(record.get("PMID"))] = clean_csl(record)
    return out


def pubmed_candidates(pmids: list[str]) -> list[dict[str, Any]]:
    if not pmids:
        return []
    records = pubmed_records(pmids)
    csl = pubmed_csl([r["pmid"] for r in records])
    out = []
    for record in records:
        entry_csl = csl.get(record["pmid"]) or {"type": "article-journal", "title": record["title"]}
        entry_csl["PMID"] = record["pmid"]
        if record["doi"]:
            entry_csl["DOI"] = record["doi"]
        if record["pmcid"]:
            entry_csl["PMCID"] = record["pmcid"]
        out.append(
            {
                "key": f"pmid:{record['pmid']}",
                "source": "PubMed",
                "csl": entry_csl,
                "headings": record["headings"],
                "pubtypes": record["pubtypes"],
                "language": record["language"],
                "retracted": record["retracted"],
                "abstract": record["abstract"],
            }
        )
    return out


# arXiv ----------------------------------------------------------------------------------------------


def arxiv_number(atom_id: str) -> str:
    return re.sub(r"v\d+$", "", atom_id.rsplit("/abs/", 1)[-1])


def arxiv_entries(search_query: str | None = None, id_list: list[str] | None = None) -> list[dict[str, Any]]:
    if id_list and len(id_list) > 100:
        return [p for i in range(0, len(id_list), 100) for p in arxiv_entries(id_list=id_list[i : i + 100])]
    entries, start = [], 0
    while True:
        params = {"start": str(start), "max_results": "100"}
        if search_query:
            params.update({"search_query": search_query, "sortBy": "submittedDate", "sortOrder": "ascending"})
        if id_list:
            params["id_list"] = ",".join(id_list)
        root = ET.fromstring(fetch(ARXIV_API + "?" + urllib.parse.urlencode(params), gap=3.1))
        page = root.findall("a:entry", ATOM)
        for item in page:
            if item.find("a:id", ATOM) is None or item.findtext("a:title", namespaces=ATOM) == "Error":
                continue
            entries.append(
                {
                    "number": arxiv_number(item.findtext("a:id", namespaces=ATOM)),
                    "title": " ".join(item.findtext("a:title", namespaces=ATOM).split()),
                    "abstract": " ".join(item.findtext("a:summary", namespaces=ATOM).split()),
                    "published": item.findtext("a:published", namespaces=ATOM)[:10],
                    "authors": [a.findtext("a:name", namespaces=ATOM) for a in item.findall("a:author", ATOM)],
                    "journal_doi": item.findtext("arxiv:doi", namespaces=ATOM),
                    "journal_ref": item.findtext("arxiv:journal_ref", namespaces=ATOM),
                }
            )
        total = int(root.findtext("{http://a9.com/-/spec/opensearch/1.1/}totalResults") or 0)
        start += len(page)
        if not page or start >= total or id_list:
            return entries


def split_name(name: str) -> dict[str, str]:
    parts = name.split()
    return {"family": parts[-1], "given": " ".join(parts[:-1])} if len(parts) > 1 else {"literal": name}


def arxiv_candidate(paper: dict[str, Any]) -> dict[str, Any]:
    number = paper["number"]
    try:
        csl = clean_csl(json.loads(fetch(f"https://doi.org/10.48550/arXiv.{number}", accept="application/vnd.citationstyles.csl+json", gap=1.0)))
    except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError):
        csl = {"author": [split_name(a) for a in paper["authors"]]}
    year, month, day = (int(x) for x in paper["published"].split("-"))
    csl.update(
        {
            "type": "article",
            "title": paper["title"],
            "issued": {"date-parts": [[year, month, day]]},
            "publisher": "arXiv",
            "number": number,
            "DOI": f"10.48550/arXiv.{number}",
            "URL": f"https://arxiv.org/abs/{number}",
        }
    )
    csl.pop("container-title", None)
    return {
        "key": f"arxiv:{number}",
        "source": "arXiv",
        "csl": csl,
        "headings": [],
        "pubtypes": ["Preprint"],
        "language": ["eng"],
        "retracted": False,
        "abstract": paper["abstract"],
        "journal_doi": paper["journal_doi"],
    }


def arxiv_search(query: str, since: dt.date, until: dt.date) -> list[dict[str, Any]]:
    window = f"submittedDate:[{since:%Y%m%d}0000 TO {until:%Y%m%d}2359]"
    return arxiv_entries(search_query=f"({query}) AND {window}")


# Full text ------------------------------------------------------------------------------------------


class _Text(HTMLParser):
    """The readable text of an HTML page: no scripts, styles, navigation or reference list."""

    SKIP = {"script", "style", "nav", "header", "footer", "math", "svg"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.skipping = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = dict(attrs).get("class") or ""
        if tag in self.SKIP or "ltx_bibliography" in classes:
            self.skipping += 1
        if tag in ("p", "h1", "h2", "h3", "h4", "li", "tr", "div", "section"):
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self.skipping:
            self.skipping -= 1

    def handle_data(self, data: str) -> None:
        if not self.skipping:
            self.parts.append(data)


def html_text(page: str) -> str:
    parser = _Text()
    parser.feed(page)
    lines = (" ".join(line.split()) for line in "".join(parser.parts).splitlines())
    return "\n".join(line for line in lines if line)


def jats_text(xml: bytes) -> str:
    root = ET.fromstring(xml)
    body = root.find(".//body")
    if body is None:
        return ""
    blocks = []
    for element in body.iter():
        if element.tag in ("title", "p"):
            blocks.append(text_of(element))
    return "\n".join(b for b in blocks if b)


def full_text(candidate: dict[str, Any], folder: Path) -> tuple[str, Path | None]:
    """Fetches an open-access paper's text into the cache. Returns ('full', path) or ('abstract', None)."""
    if candidate.get("text"):
        return "full", Path(candidate["text"])
    texts = folder / "texts"
    texts.mkdir(parents=True, exist_ok=True)
    name = re.sub(r"[^\w.-]", "-", candidate["key"])
    csl = candidate["csl"]
    try:
        if csl.get("PMCID"):
            text = jats_text(ncbi("efetch.fcgi", {"db": "pmc", "id": csl["PMCID"], "retmode": "xml"}))
            if len(text) >= MIN_FULL_TEXT:
                path = texts / f"{name}.txt"
                path.write_text(text[:MAX_TEXT], encoding="utf-8")
                return "full", path
        if csl.get("publisher") == "arXiv":
            try:
                text = html_text(fetch(f"https://arxiv.org/html/{csl['number']}", gap=3.1).decode("utf-8", "replace"))
            except urllib.error.HTTPError:
                text = ""
            if len(text) >= MIN_FULL_TEXT:
                path = texts / f"{name}.txt"
                path.write_text(text[:MAX_TEXT], encoding="utf-8")
                return "full", path
            pdf = fetch(f"https://arxiv.org/pdf/{csl['number']}", gap=3.1)
            if pdf.startswith(b"%PDF"):
                path = texts / f"{name}.pdf"
                path.write_bytes(pdf)
                return "full", path
    except (urllib.error.HTTPError, urllib.error.URLError, ET.ParseError):
        pass
    return "abstract", None


# Search, seed and re-check --------------------------------------------------------------------------


def known_keys() -> set[str]:
    keys: set[str] = set()
    for entry in library.load(library.REFERENCES):
        keys |= library.identifiers(entry)
    for row in library.load(library.SCREENED_OUT):
        keys |= library.identifiers(row)
    return keys


def known_titles() -> set[str]:
    rows = library.load(library.REFERENCES) + library.load(library.SCREENED_OUT)
    return {same_title(r.get("title", "")) for r in rows if r.get("title")}


def same_title(title: str) -> str:
    return "".join(re.findall(r"[a-z0-9]+", title.lower()))


def new_only(candidates: list[dict[str, Any]], known: set[str], known_titles: set[str] = frozenset()) -> list[dict[str, Any]]:
    """Drops what the Library holds or screened out, and a paper found twice. The arXiv copy of a
    paper a journal has published gives way to the journal's record, matched by the DOI arXiv gives
    or, when it gives none, by the same title."""
    out, seen = [], set(known)
    # A published version is kept over its preprint, which PubMed also lists (medRxiv, arXiv).
    candidates = sorted(candidates, key=lambda c: c["source"] == "arXiv" or "Preprint" in c.get("pubtypes", []))
    published_copies = [c for c in candidates if c["source"] != "arXiv"]
    journal_dois = {c["csl"]["DOI"].lower() for c in published_copies if c["csl"].get("DOI")}
    journal_titles = {same_title(c["csl"].get("title", "")) for c in published_copies}
    titles = set(known_titles)
    for candidate in candidates:
        keys = library.identifiers(candidate["csl"])
        title = same_title(candidate["csl"].get("title", ""))
        published = (candidate.get("journal_doi") or "").lower()
        if keys & seen or title in titles:
            continue
        if candidate["source"] == "arXiv" and (
            (published and ("doi:" + published in seen or published in journal_dois)) or title in journal_titles
        ):
            continue
        seen |= keys
        titles.add(title)
        out.append(candidate)
    return out


def area_folder(area_id: str) -> Path:
    folder = library.CACHE / area_id
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def last_search_to(area_id: str) -> dt.date | None:
    mine = [s for s in library.load(library.SEARCHES) if s.get("area") == area_id]
    return dt.date.fromisoformat(max(s["window"]["to"] for s in mine)) if mine else None


def run_search(area: dict[str, Any], since: dt.date, until: dt.date) -> dict[str, Any]:
    known = known_keys()
    pmids = pubmed_search(area["search"]["pubmed"], since, until)
    pubmed = pubmed_candidates([p for p in pmids if f"pmid:{p}" not in known])
    papers = arxiv_search(area["search"]["arxiv"], since, until)
    arxiv = [arxiv_candidate(p) for p in papers if f"arxiv:{p['number']}" not in known]
    candidates = new_only(pubmed + arxiv, known, known_titles())
    folder = area_folder(area["id"])
    run = {
        "area": area["id"],
        "date": dt.datetime.now(dt.timezone.utc).date().isoformat(),
        "window": {"from": since.isoformat(), "to": until.isoformat()},
        "databases": [
            {"name": "PubMed", "query": area["search"]["pubmed"], "found": len(pmids), "new": sum(c["source"] == "PubMed" for c in candidates)},
            {"name": "arXiv", "query": area["search"]["arxiv"], "found": len(papers), "new": sum(c["source"] == "arXiv" for c in candidates)},
        ],
    }
    library.dump(folder / "candidates.json", candidates)
    library.dump(folder / "run.json", run)
    return run


def seed(area: dict[str, Any], given: list[str], manual: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Papers named by hand: pmid:N, arxiv:N or a DOI. Each is screened like a search's. A work no
    database holds, such as an organisation's survey report, comes in `manual` as its CSL and the
    path of its text or PDF."""
    known = known_keys()
    pmids = [g.split(":", 1)[1] for g in given if g.startswith("pmid:")]
    numbers = [g.split(":", 1)[1] for g in given if g.startswith("arxiv:")]
    dois = [g for g in given if g.startswith("10.")]
    candidates = pubmed_candidates(pmids)
    candidates += [arxiv_candidate(p) for p in (arxiv_entries(id_list=numbers) if numbers else [])]
    for doi in dois:
        # A DOI PubMed knows is read from PubMed, for its subject headings; any other from its agency.
        found = json.loads(ncbi("esearch.fcgi", {"db": "pubmed", "term": f"{doi}[doi]", "retmode": "json"}))["esearchresult"]["idlist"]
        if len(found) == 1:
            candidates += pubmed_candidates(found)
            continue
        raw = json.loads(fetch(f"https://doi.org/{doi}", accept="application/vnd.citationstyles.csl+json", gap=1.0))
        csl = clean_csl(raw)
        csl["DOI"] = doi
        # The abstract is read to screen and summarize the paper, and kept in the cache only.
        abstract = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", raw.get("abstract") or "")).split())
        candidates.append({"key": f"doi:{doi.lower()}", "source": "DOI", "csl": csl, "headings": [], "pubtypes": [], "language": [], "retracted": False, "abstract": abstract})
    for work in manual or []:
        csl = clean_csl(work["csl"])
        candidates.append(
            {"key": f"url:{csl['URL']}", "source": "URL", "csl": csl, "headings": [], "pubtypes": [], "language": ["eng"], "retracted": False, "abstract": "", "text": work["text"]}
        )
    folder = area_folder(area["id"])
    path = folder / "candidates.json"
    existing = library.load(path) if path.exists() else []
    # What a search already put in the cache stays as found; a seed it also found is not added twice.
    titles = known_titles()
    for candidate in existing:
        known |= library.identifiers(candidate["csl"])
        titles.add(same_title(candidate["csl"].get("title", "")))
    candidates = [dict(c, seed=True) for c in new_only(candidates, known, titles)]
    library.dump(path, existing + candidates)
    return candidates


def recheck(today: dt.date) -> dict[str, Any]:
    """Every entry's DOI or address, PubMed's retraction flag, and whether an arXiv preprint has been
    published. Sets `checked` on each entry it could check."""
    entries = library.load(library.REFERENCES)
    findings, checked = [], 0
    pmids = [x["PMID"] for x in entries if x.get("PMID")]
    records = {r["pmid"]: r for r in pubmed_records(pmids)} if pmids else {}
    retracted = {pmid for pmid, r in records.items() if r["retracted"]}
    # NLM adds a record's subject headings weeks after the record appears, so they are read again.
    for entry in entries:
        record = records.get(entry.get("PMID"))
        if record and record["headings"] and record["headings"] != entry["custom"]["headings"]:
            entry["custom"]["headings"] = record["headings"]
    numbers = [x["number"] for x in entries if x.get("publisher") == "arXiv" and x.get("number")]
    published = {p["number"]: p["journal_doi"] for p in arxiv_entries(id_list=numbers) if p["journal_doi"]} if numbers else {}
    for entry in entries:
        try:
            if entry.get("DOI"):
                handle = json.loads(fetch(f"https://doi.org/api/handles/{urllib.parse.quote(entry['DOI'])}", gap=0.5))
                if handle.get("responseCode") != 1:
                    findings.append({"id": entry["id"], "finding": f"its DOI {entry['DOI']} no longer resolves"})
            else:
                fetch(entry["URL"], gap=1.0)
        except urllib.error.HTTPError as error:
            findings.append({"id": entry["id"], "finding": f"its address answers {error.code}"})
        except urllib.error.URLError:
            findings.append({"id": entry["id"], "finding": "its address could not be reached"})
            continue
        if entry.get("PMID") in retracted:
            findings.append({"id": entry["id"], "finding": "PubMed marks it retracted"})
        if entry.get("number") in published:
            findings.append({"id": entry["id"], "finding": f"the preprint has been published since, as {published[entry['number']]}"})
        entry["custom"]["checked"] = today.isoformat()
        checked += 1
    library.save_references(entries)
    result = {"entries": checked, "findings": findings}
    library.CACHE.mkdir(parents=True, exist_ok=True)
    library.dump(library.CACHE / "recheck.json", result)
    return result


def area_by_id(area_id: str) -> dict[str, Any]:
    for area in library.areas():
        if area["id"] == area_id:
            return area
    raise SystemExit(f"no area {area_id!r} in library/areas")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    search_cmd = commands.add_parser("search")
    search_cmd.add_argument("--area", required=True)
    search_cmd.add_argument("--since", type=dt.date.fromisoformat)
    search_cmd.add_argument("--until", type=dt.date.fromisoformat)
    seed_cmd = commands.add_parser("seed")
    seed_cmd.add_argument("--area", required=True)
    seed_cmd.add_argument("ids", nargs="*")
    seed_cmd.add_argument("--manual", type=Path, help="a JSON list of {csl, text} for works no database holds")
    commands.add_parser("recheck")
    args = parser.parse_args(argv)
    today = dt.datetime.now(dt.timezone.utc).date()

    if args.command == "search":
        area = area_by_id(args.area)
        # One search of an area a day: a search is named by its date, which its entries and the
        # papers it set aside carry. A second run that day changes nothing and says so.
        if any(s.get("area") == area["id"] and s.get("date") == today.isoformat() for s in library.load(library.SEARCHES)):
            print(f"{area['id']}: already searched on {today.isoformat()}; nothing to do", file=sys.stderr)
            return 0
        last = last_search_to(area["id"])
        start = dt.date.fromisoformat(area["from"])
        since = args.since or (max(start, last - dt.timedelta(days=7)) if last else start)
        run = run_search(area, since, args.until or today)
        for database in run["databases"]:
            print(f"{database['name']}: {database['found']} found, {database['new']} new", file=sys.stderr)
        # The workflow's later steps run only when some area was searched.
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
                output.write("searched=true\n")
        return 0
    if args.command == "seed":
        manual = library.load(args.manual) if args.manual else []
        added = seed(area_by_id(args.area), args.ids, manual)
        print(f"seed: {len(added)} new of {len(args.ids) + len(manual)} named", file=sys.stderr)
        return 0
    if args.command == "recheck":
        result = recheck(today)
        print(f"recheck: {result['entries']} checked, {len(result['findings'])} findings", file=sys.stderr)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

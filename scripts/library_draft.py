"""Screens the papers a search found, and drafts an entry for each one in scope, with Claude.

    uv run --group library --env-file=.env python scripts/library_draft.py --area caregiving-and-ai [--batch]

It reads what scripts/library_search.py left in library/.cache/<area>/: the candidates and the
search's own record. Each paper is screened on its title, abstract and record. For each one in
scope it fetches the full text when the paper is open (PubMed Central or arXiv) and asks for the
entry's fields as structured output, under the instructions in library/prompt.md. It writes:

    library/references.json    each new entry, marked as a draft for a person to approve
    library/screened-out.json  each paper set aside, with its reason
    library/searches.json      the search's record: what was found, the model, its tokens and cost

A summary that repeats any run of eight words from the paper's abstract is drafted again once,
told which words; if it still repeats them the entry says so for the reviewer. No abstract or full
text is written anywhere but the cache.

--batch sends the requests through the Message Batches API, at half price, for a large first search
run on a laptop; a batch can take hours. The monthly workflow calls the Messages API directly. A
paper the model declined to screen or draft is listed in the search's record and its pull request.

The key is ANTHROPIC_API_KEY, from the environment: on a laptop `--env-file .env` loads it, and the
monthly workflow passes its secret. --keys limits a run to named candidates (pmid:N, arxiv:N), for a
trial or to draft one paper again.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import library  # noqa: E402
import library_search  # noqa: E402

MODEL = "claude-opus-5-5"
PROMPT = library.LIBRARY / "prompt.md"
PRICES = library.LIBRARY / "prices.json"
OVERLAP_WORDS = 8
# Screening reasons the model may give; the last two are a person's or the search's own.
SCREEN_REASONS = [r for r in library.REASONS if r not in ("removed on review", "the same work as another entry")]

SCREEN_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["include", "exclude"]},
        "reason": {"type": "string", "enum": ["in scope", *SCREEN_REASONS]},
        "borderline": {"type": "boolean"},
        "note": {"type": "string"},
    },
    "required": ["decision", "reason", "borderline", "note"],
    "additionalProperties": False,
}


def draft_schema(theme_ids: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "themes": {"type": "array", "items": {"type": "string", "enum": theme_ids}},
            "evidence": {"type": "string", "enum": list(library.EVIDENCE)},
            "studied": {"type": "string"},
            "sample_n": {"type": "integer"},
            "sample_of": {"type": "string"},
            "peer_reviewed": {"type": "boolean"},
            "review_note": {"type": "string"},
        },
        "required": ["summary", "themes", "evidence", "studied", "sample_n", "sample_of", "peer_reviewed", "review_note"],
        "additionalProperties": False,
    }


# The prompt -----------------------------------------------------------------------------------------


def system_prompt(area: dict[str, Any], themes: list[dict[str, Any]], search_date: str) -> str:
    """library/prompt.md after its first rule, with the search's date, the area's scope and the lists
    filled in."""
    text = PROMPT.read_text(encoding="utf-8").split("\n---\n", 1)[1].strip()
    return (
        text.replace("{search_date}", library.iso_long(search_date))
        .replace("{area_title}", area["title"])
        .replace("{area_include}", "\n".join(f"- {line}" for line in area["include"]))
        .replace("{area_exclude}", "\n".join(f"- {line}" for line in area["exclude"]))
        .replace("{area_theme}", area["theme"])
        .replace("{evidence_list}", "\n".join(f"- {kind}" for kind in library.EVIDENCE))
        .replace("{theme_list}", "\n".join(f"- `{t['id']}`: {t['name']}. {t['definition']}" for t in themes))
    )


def record_text(candidate: dict[str, Any]) -> str:
    csl = candidate["csl"]
    year, month, day = library.date_parts(csl)
    lines = [
        f"Title: {csl.get('title', '')}",
        f"Authors: {library.authors(csl, shown=12)}",
        f"Published in: {library.venue(csl) or 'not stated'}",
        f"Date: {library.long_date(year, month, day) if year else 'not stated'}",
        f"Record type: {csl.get('type', '')}; from {candidate['source']}",
    ]
    if candidate.get("pubtypes"):
        lines.append(f"PubMed publication types: {', '.join(candidate['pubtypes'])}")
    if candidate.get("headings"):
        lines.append(f"PubMed subject headings: {', '.join(h['name'] for h in candidate['headings'])}")
    if candidate.get("language"):
        lines.append(f"Language: {', '.join(candidate['language'])}")
    return "\n".join(lines)


def screen_request(candidate: dict[str, Any], system: list[dict[str, Any]]) -> dict[str, Any]:
    abstract = candidate.get("abstract") or "No abstract is available."
    return {
        "model": MODEL,
        "max_tokens": 4000,
        "system": system,
        "output_config": {"effort": "low", "format": {"type": "json_schema", "schema": SCREEN_SCHEMA}},
        "messages": [{"role": "user", "content": f"Screen this paper.\n\n{record_text(candidate)}\n\nAbstract:\n{abstract}"}],
    }


def draft_request(candidate: dict[str, Any], read: str, path: Path | None, system: list, schema: dict, avoid: str = "") -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    if read == "full" and path is not None and path.suffix == ".pdf":
        data = base64.standard_b64encode(path.read_bytes()).decode()
        content.append({"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": data}})
        body = "The paper's full text is the document above."
    elif read == "full" and path is not None:
        body = "The paper's full text:\n\n" + path.read_text(encoding="utf-8")
    else:
        body = "Only the abstract could be read:\n\n" + (candidate.get("abstract") or "No abstract is available.")
    ask = f"Draft the entry for this paper.\n\n{record_text(candidate)}"
    if avoid:
        ask += f"\n\nAn earlier draft repeated these words from the abstract: \"{avoid}\". Write the summary without them."
    content.append({"type": "text", "text": f"{ask}\n\n{body}"})
    return {
        "model": MODEL,
        "max_tokens": 16000,
        "system": system,
        "output_config": {"effort": "high", "format": {"type": "json_schema", "schema": schema}},
        "messages": [{"role": "user", "content": content}],
    }


# Calling the model ----------------------------------------------------------------------------------


class Usage:
    def __init__(self) -> None:
        self.tokens = {"input": 0, "cache_write": 0, "cache_read": 0, "output": 0}
        self.batch_tokens = {"input": 0, "cache_write": 0, "cache_read": 0, "output": 0}

    def add(self, usage: Any, batch: bool) -> None:
        into = self.batch_tokens if batch else self.tokens
        into["input"] += usage.input_tokens or 0
        into["cache_write"] += getattr(usage, "cache_creation_input_tokens", 0) or 0
        into["cache_read"] += getattr(usage, "cache_read_input_tokens", 0) or 0
        into["output"] += usage.output_tokens or 0

    def cost(self) -> float:
        prices = library.load(PRICES)
        rate = prices["models"][MODEL]

        def dollars(t: dict[str, int]) -> float:
            return (
                t["input"] * rate["input"]
                + t["cache_write"] * rate["cache_write_5m"]
                + t["cache_read"] * rate["cache_read"]
                + t["output"] * rate["output"]
            ) / 1_000_000

        return round(dollars(self.tokens) + dollars(self.batch_tokens) * prices["batch_multiplier"], 4)

    def total(self) -> dict[str, int]:
        return {k: self.tokens[k] + self.batch_tokens[k] for k in self.tokens}


def answer(message: Any) -> tuple[dict[str, Any] | None, str]:
    """The structured answer of a finished message, or None and why there is none."""
    if message.stop_reason == "refusal":
        return None, "the model declined"
    if message.stop_reason == "max_tokens":
        return None, "the answer was cut off"
    text = next((b.text for b in message.content if b.type == "text"), "")
    try:
        return json.loads(text), ""
    except json.JSONDecodeError:
        return None, "the answer was not valid JSON"


def custom_id(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", key)[:64]


def run_direct(client: Any, requests: dict[str, dict[str, Any]], usage: Usage) -> dict[str, tuple[dict | None, str]]:
    out = {}
    for key, params in requests.items():
        try:
            message = client.messages.create(**params)
        except Exception as error:  # noqa: BLE001 - recorded for the reviewer, never hidden
            out[key] = (None, f"the request failed: {type(error).__name__}")
            continue
        usage.add(message.usage, batch=False)
        out[key] = answer(message)
    return out


def run_batch(client: Any, requests: dict[str, dict[str, Any]], usage: Usage, state: Path) -> dict[str, tuple[dict | None, str]]:
    """Sends the requests as one batch, waits for it, and reads every result. A batch already sent
    for the same requests (its id kept in `state`) is waited for instead of sent again."""
    if not requests:
        return {}
    ids = {custom_id(k): k for k in requests}
    if state.exists() and set(library.load(state)["ids"]) == set(ids):
        batch_id = library.load(state)["batch"]
    else:
        batch = client.messages.batches.create(requests=[{"custom_id": custom_id(k), "params": p} for k, p in requests.items()])
        batch_id = batch.id
        library.dump(state, {"batch": batch_id, "ids": sorted(ids)})
    while True:
        batch = client.messages.batches.retrieve(batch_id)
        if batch.processing_status == "ended":
            break
        counts = batch.request_counts
        print(f"batch {batch_id}: {counts.processing} processing, {counts.succeeded} done", file=sys.stderr)
        time.sleep(60)
    out: dict[str, tuple[dict | None, str]] = {}
    for result in client.messages.batches.results(batch_id):
        key = ids[result.custom_id]
        if result.result.type == "succeeded":
            usage.add(result.result.message.usage, batch=True)
            out[key] = answer(result.result.message)
        else:
            out[key] = (None, f"the batch request {result.result.type}")
    return out


# Words in common with the abstract ------------------------------------------------------------------


def shingles(text: str, size: int = OVERLAP_WORDS) -> dict[tuple[str, ...], str]:
    words = re.findall(r"[a-z0-9]+(?:['’][a-z]+)?", text.lower())
    return {tuple(words[i : i + size]): " ".join(words[i : i + size]) for i in range(len(words) - size + 1)}


def repeated(summary: str, abstract: str) -> str:
    """The first run of eight words the summary shares with the abstract, or an empty string."""
    if not abstract:
        return ""
    theirs = shingles(abstract)
    for key, words in shingles(summary).items():
        if key in theirs:
            return words
    return ""


# Turning answers into entries -----------------------------------------------------------------------


def screened_out_row(candidate: dict[str, Any], reason: str, note: str, search: str) -> dict[str, Any]:
    csl = candidate["csl"]
    row: dict[str, Any] = {"title": csl.get("title", "")}
    if csl.get("DOI"):
        row["doi"] = csl["DOI"]
    if csl.get("PMID"):
        row["pmid"] = csl["PMID"]
    if csl.get("publisher") == "arXiv" and csl.get("number"):
        row["arxiv"] = csl["number"]
    if not library.identifiers(row):
        row["url"] = csl.get("URL")
    row.update({"reason": reason, "note": note, "search": search})
    return row


def entry_from(candidate: dict[str, Any], fields: dict[str, Any], read: str, area: dict[str, Any], search: str, taken: set[str], notes: list[str]) -> dict[str, Any]:
    csl = {k: v for k, v in candidate["csl"].items()}
    csl["id"] = library_search.entry_id(csl, taken)
    # A PubMed record with no DOI, such as a conference paper's, is linked by its PubMed page.
    if not csl.get("DOI") and not csl.get("URL") and csl.get("PMID"):
        csl["URL"] = f"https://pubmed.ncbi.nlm.nih.gov/{csl['PMID']}/"
    themes = [area["theme"]] + [t for t in dict.fromkeys(fields["themes"]) if t != area["theme"]]
    custom: dict[str, Any] = {
        "summary": " ".join(fields["summary"].split()),
        "themes": themes,
        "evidence": fields["evidence"],
        "studied": fields["studied"].strip(),
    }
    if fields["sample_n"] > 0 and fields["sample_of"].strip():
        custom["sample"] = {"n": fields["sample_n"], "of": fields["sample_of"].strip()}
    custom.update(
        {
            "peer-reviewed": fields["peer_reviewed"],
            "read": read,
            "headings": candidate.get("headings", []),
            "checked": dt.datetime.now(dt.timezone.utc).date().isoformat(),
            "found": "seed" if candidate.get("seed") else f"search {search}",
            "drafted-by": MODEL,
        }
    )
    review = [n for n in (*notes, fields["review_note"].strip()) if n]
    if review:
        custom["review-note"] = " ".join(review)
    custom["draft"] = True
    csl["custom"] = custom
    return csl


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--area", required=True)
    parser.add_argument("--batch", action="store_true", help="use the Message Batches API (half price, slow)")
    parser.add_argument("--limit", type=int, help="only the first N candidates, for a trial")
    parser.add_argument("--keys", nargs="+", help="only these candidates (pmid:N, arxiv:N)")
    parser.add_argument("--retry-failed", action="store_true", help="the area's last search's failed papers, added to its record")
    args = parser.parse_args(argv)

    import anthropic

    client = anthropic.Anthropic()
    area = library_search.area_by_id(args.area)
    themes = library.themes()
    folder = library_search.area_folder(area["id"])
    candidates = library.load(folder / "candidates.json") if (folder / "candidates.json").exists() else []
    if args.keys:
        candidates = [c for c in candidates if c["key"] in set(args.keys)]
    if args.limit:
        candidates = candidates[: args.limit]
    run = library.load(folder / "run.json")
    if args.retry_failed:
        # The papers the area's last search could not screen or draft, run again under that search.
        run = library.latest_search(area["id"], library.load(library.SEARCHES))
        failed_keys = {f["key"] for f in run.get("failed", [])}
        candidates = [c for c in candidates if c["key"] in failed_keys]
    search = run["date"]
    system = [{"type": "text", "text": system_prompt(area, themes, search), "cache_control": {"type": "ephemeral"}}]
    usage = Usage()
    by_key = {c["key"]: c for c in candidates}

    def send(requests: dict[str, dict[str, Any]], name: str) -> dict[str, tuple[dict | None, str]]:
        if args.batch:
            return run_batch(client, requests, usage, folder / f"batch-{name}.json")
        return run_direct(client, requests, usage)

    screened = send({k: screen_request(c, system) for k, c in by_key.items()}, "screen")
    failed, rows, included = [], [], {}
    for key, (decision, why) in screened.items():
        candidate = by_key[key]
        if decision is None:
            failed.append({"key": key, "title": candidate["csl"].get("title", ""), "why": f"screening: {why}"})
        elif decision["decision"] == "include":
            included[key] = decision
        else:
            reason = decision["reason"] if decision["reason"] in SCREEN_REASONS else SCREEN_REASONS[0]
            rows.append(screened_out_row(candidate, reason, decision["note"].strip(), search))

    texts = {key: library_search.full_text(by_key[key], folder) for key in included}
    schema = draft_schema([t["id"] for t in themes])
    drafted = send({k: draft_request(by_key[k], texts[k][0], texts[k][1], system, schema) for k in included}, "draft")
    # A summary that repeats the abstract is drafted once more, directly, told which words.
    again = {}
    for key, (fields, _) in drafted.items():
        words = repeated(fields["summary"], by_key[key].get("abstract", "")) if fields else ""
        if words:
            again[key] = draft_request(by_key[key], texts[key][0], texts[key][1], system, schema, avoid=words)
    for key, result in run_direct(client, again, usage).items():
        if result[0] is not None:
            drafted[key] = result

    entries = library.load(library.REFERENCES)
    taken = {e["id"] for e in entries}
    theme_ids = {t["id"] for t in themes}
    new = 0
    for key, (fields, why) in drafted.items():
        candidate = by_key[key]
        if fields is None:
            failed.append({"key": key, "title": candidate["csl"].get("title", ""), "why": f"drafting: {why}"})
            continue
        notes = []
        if included[key]["borderline"] and included[key]["note"].strip():
            notes.append(f"Borderline: {included[key]['note'].strip()}")
        words = repeated(fields["summary"], candidate.get("abstract", ""))
        if words:
            notes.append(f"The summary still repeats the abstract's words \"{words}\".")
        entry = entry_from(candidate, fields, texts[key][0], area, search, taken, notes)
        problems = library.entry_problems(entry, theme_ids, library.csl_validator())
        if problems:
            failed.append({"key": key, "title": entry["title"], "why": "drafting: " + "; ".join(problems)})
            continue
        taken.add(entry["id"])
        entries.append(entry)
        new += 1
    library.save_references(entries)
    library.dump(library.SCREENED_OUT, library.load(library.SCREENED_OUT) + rows)

    recheck_path = library.CACHE / "recheck.json"
    if args.retry_failed:
        # The same search, so its counts grow and its failures are what is still failing.
        record = dict(run)
        record["screened-out"] = run["screened-out"] + len(rows)
        record["drafted"] = run["drafted"] + new
        record["failed"] = failed
        record["tokens"] = {k: run["tokens"][k] + v for k, v in usage.total().items()}
        record["cost-usd"] = round(run["cost-usd"] + usage.cost(), 4)
    else:
        record = {
            **run,
            "seeded": sum(1 for c in candidates if c.get("seed")),
            "screened": len(candidates),
            "screened-out": len(rows),
            "drafted": new,
            "failed": failed,
            "recheck": library.load(recheck_path) if recheck_path.exists() else {"entries": 0, "findings": []},
            "model": MODEL,
            "batch": args.batch,
            "tokens": usage.total(),
            "cost-usd": usage.cost(),
        }
    searches = [s for s in library.load(library.SEARCHES) if not (s.get("area") == area["id"] and s.get("date") == search)]
    library.dump(library.SEARCHES, searches + [record])
    print(
        f"{area['id']}: {len(candidates)} screened, {len(rows)} screened out, {new} drafted, "
        f"{len(failed)} failed; ${record['cost-usd']:.2f}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

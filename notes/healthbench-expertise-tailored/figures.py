"""Compute every number on this note from its logs, and write the three files the page shows.

    _results.yml        the numbers, as text, read by the page through {{< meta >}}
    _table-scores.md    the scores by user group, included by the page
    _table-axes.md      the scores by axis, included by the page
    _chart.md           the chart, inline SVG, included by the page

Run by scripts/note_numbers.py before every build, so a log that changes changes the page, and a log
that is missing stops the build with its name. Nothing here is typed by hand except the
prices, which prices.toml holds with the page and date each was read from.
"""

from __future__ import annotations

import json
import sys
import tomllib
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "scripts"))

import chart  # noqa: E402
import stats  # noqa: E402
from inspect_ai.log import EvalLog, read_eval_log  # noqa: E402

RUNS = ("check", "opus", "gemma")
NAMES = {"opus": "Claude Opus 5.5", "gemma": "Gemma 3 12B on the laptop"}
# The judge a log names in its metadata, or this one (decision of 2026-11-25, after the judge trial).
JUDGE = "openai/gpt-4.1"
GROUPS = {
    "layperson": "physician_agreed_category:not-health-professional",
    "professional": "physician_agreed_category:health-professional",
}
GROUP_NAMES = {"all": "All conversations", "layperson": "The user is a layperson", "professional": "The user is a health professional"}
AXES = ("accuracy", "completeness", "communication_quality", "context_awareness", "instruction_following")
AXIS_NAMES = {
    "accuracy": "Accuracy",
    "completeness": "Completeness",
    "communication_quality": "Communication quality",
    "context_awareness": "Context awareness",
    "instruction_following": "Instruction following",
}
# What HealthBench's scorer writes when the judge never gave a usable answer for a criterion; the
# criterion then counts as not met.
JUDGE_FAILED = ("No explanation provided", "Failed to parse judge response")
# The judge's bar, fixed before any judge result: the median physician (decision of 2026-11-25).
BAR_QUANTILE = 0.5


class MissingLog(Exception):
    pass


def find_logs(logs: Path) -> dict[str, Path]:
    """One log per run, found by the run name each log carries in its metadata."""
    found: dict[str, list[Path]] = defaultdict(list)
    for path in sorted(logs.glob("*.eval")):
        header = read_eval_log(str(path), header_only=True)
        found[(header.eval.metadata or {}).get("run", "")].append(path)
    for run in RUNS:
        if not found.get(run):
            raise MissingLog(f"{logs}: no log for the {run!r} run")
        if len(found[run]) > 1:
            raise MissingLog(f"{logs}: {len(found[run])} logs for the {run!r} run: {[p.name for p in found[run]]}")
    return {run: found[run][0] for run in RUNS}


def judge_of(log: EvalLog) -> str:
    return (log.eval.metadata or {}).get("judge", JUDGE)


def complete(log: EvalLog, path: Path) -> None:
    if log.status != "success":
        raise MissingLog(f"{path.name}: the run ended as {log.status!r}")
    expected = (log.eval.metadata or {}).get("samples")
    errors = [s.id for s in log.samples or [] if s.error]
    if errors or (expected is not None and len(log.samples or []) != expected):
        raise MissingLog(f"{path.name}: {len(log.samples or [])} of {expected} samples, {len(errors)} with errors")


@dataclass
class ModelRun:
    log: EvalLog
    score: dict[str, float] = field(default_factory=dict)
    adjusted: dict[str, float] = field(default_factory=dict)
    length: dict[str, int] = field(default_factory=dict)
    group: dict[str, str] = field(default_factory=dict)
    axis: dict[str, dict[str, float]] = field(default_factory=lambda: defaultdict(dict))
    capped: int = 0
    filtered: int = 0
    criteria: int = 0
    shared: int = 0
    judge_failed: int = 0
    versions: set[str] = field(default_factory=set)
    judge_versions: set[str] = field(default_factory=set)


def read_model_run(path: Path) -> ModelRun:
    log = read_eval_log(str(path))
    complete(log, path)
    run = ModelRun(log)
    judge = judge_of(log)
    for sample in log.samples or []:
        sid = str(sample.id)
        value = next(iter(sample.scores.values()))
        run.score[sid] = float(value.value["healthbench_score"])
        run.adjusted[sid] = float(value.value["healthbench_score_length_adjusted"])
        run.length[sid] = len(sample.output.completion)
        run.capped += sample.output.stop_reason == "max_tokens"
        run.filtered += sample.output.stop_reason == "content_filter"
        if sample.output.model:
            run.versions.add(sample.output.model)
        for name, tag in GROUPS.items():
            if tag in sample.metadata["example_tags"]:
                run.group[sid] = name
        criteria = value.metadata["detailed_results"]
        run.criteria += len(criteria)
        # The theme's shared criteria, one checklist per kind of user; the judge check covers these.
        run.shared += sum("level:cluster" in c["tags"] for c in criteria)
        run.judge_failed += sum(str(c["explanation"]).startswith(JUDGE_FAILED) for c in criteria)
        for axis in AXES:
            tagged = [c for c in criteria if f"axis:{axis}" in c["tags"]]
            possible = sum(c["points"] for c in tagged if c["points"] > 0)
            if possible > 0:
                run.axis[axis][sid] = sum(c["points"] for c in tagged if c["met"]) / possible
        for event in sample.events:
            # A call that failed and was sent again names no dated version, so only answers count.
            if event.event == "model" and event.model == judge and not event.error and event.output and event.output.model:
                run.judge_versions.add(event.output.model)
    return run


def judge_answered(text: str) -> bool:
    """Whether a judge's answer holds the JSON verdict HealthBench's grader asks for."""
    start, end = text.find("{"), text.rfind("}")
    try:
        verdict = json.loads(text[start : end + 1]) if start >= 0 else {}
    except json.JSONDecodeError:
        return False
    return isinstance(verdict, dict) and isinstance(verdict.get("criteria_met"), bool)


@dataclass
class CheckRun:
    log: EvalLog
    items: dict[str, list[stats.Grades]]
    judge_failed: int
    inspect_majority_f1: float
    judge_versions: set[str]


def read_check_run(path: Path) -> CheckRun:
    log = read_eval_log(str(path))
    complete(log, path)
    items: dict[str, list[stats.Grades]] = defaultdict(list)
    failed = 0
    versions: set[str] = set()
    judge = judge_of(log)
    for sample in log.samples or []:
        meta = sample.metadata
        value = next(iter(sample.scores.values()))
        grade = stats.Grades(meta["anonymized_physician_ids"], meta["binary_labels"], value.value["model_grade"] == "C")
        group = "professional" if "_health-professional_" in meta["category"] else "layperson"
        items["all"].append(grade)
        items[group].append(grade)
        calls = [e for e in sample.events if e.event == "model" and e.model == judge]
        versions |= {e.output.model for e in calls if e.output and e.output.model}
        # The scorer retries a judge answer it cannot read, then counts the criterion as not met.
        failed += not calls or not judge_answered(calls[-1].output.completion if calls[-1].output else "")
    majority = log.results.scores[0].metrics["macro_f1"].value if log.results and log.results.scores else float("nan")
    return CheckRun(log, items, failed, float(majority), versions)


TRIAL_NAMES = {
    "gpt-4.1": "GPT-4.1, HealthBench's own judge",
    "haiku": "Claude Haiku 5.5",
    "luna-high": "gpt-6-luna, high effort",
    "luna-default": "gpt-6-luna, default",
    "terra": "gpt-6.1-terra",
    "luna-no-reasoning": "gpt-6-luna, no reasoning",
}


def read_trial(trial: Path, prices: dict) -> dict:
    """Every judge tried on the same random rows of the judge check, before one was chosen. The first
    judge, gpt-6-luna at its default settings, ran on all the rows first; its log of that is on the
    release (over the repository's size limit), and its log here is the same rows from that run,
    through Inspect's cache."""
    paths: dict[str, Path] = {}
    for path in sorted(trial.glob("*.eval")):
        run = (read_eval_log(str(path), header_only=True).eval.metadata or {}).get("run", "")
        name = run.removeprefix("judge-trial-") if run.startswith("judge-trial-") else ""
        if name in TRIAL_NAMES:
            paths[name] = path
    missing = sorted(set(TRIAL_NAMES) - set(paths))
    if missing:
        raise MissingLog(f"{trial}: no trial log for {missing}")
    logs = {name: read_eval_log(str(path)) for name, path in paths.items()}
    rows = {str(s.id) for s in logs["gpt-4.1"].samples or []}
    judges, physicians_items = [], []
    for name, log in logs.items():
        items = []
        for sample in log.samples or []:
            if str(sample.id) in rows:
                grade = next(iter(sample.scores.values())).value["model_grade"] == "C"
                meta = sample.metadata
                items.append(stats.Grades(meta["anonymized_physician_ids"], meta["binary_labels"], grade))
        if len(items) != len(rows):
            raise MissingLog(f"{paths[name].name}: {len(items)} of the trial's {len(rows)} rows")
        physicians_items = items
        judges.append(
            {
                "name": TRIAL_NAMES[name],
                "f1": stats.judge_f1(items),
                "met": sum(i.model for i in items) / len(items),
                # What this judge costs on these rows, each call once, though luna's came from the cache.
                "cost": cost(log, prices, rows, reused=True),
            }
        )
    physicians = stats.physician_agreement(physicians_items)
    met = sum(sum(i.grades) for i in physicians_items) / sum(len(i.grades) for i in physicians_items)
    # The current models tried, apart from GPT-4.1, HealthBench's own judge.
    current = [j["f1"] for j in judges if j["name"] != TRIAL_NAMES["gpt-4.1"]]
    return {
        "gpt41_f1": f"{next(j['f1'] for j in judges if j['name'] == TRIAL_NAMES['gpt-4.1']):.3f}",
        "current_low": f"{min(current):.3f}",
        "current_high": f"{max(current):.3f}",
        "rows": f"{len(rows):,}",
        "seed": str((logs["gpt-4.1"].eval.metadata or {}).get("seed", "")),
        "physicians_met": f"{met:.0%}",
        "physicians_weighted": f"{physicians.weighted:.3f}",
        "judges": [
            {"name": j["name"], "f1": f"{j['f1']:.3f}", "met": f"{j['met']:.0%}", "cost": dollars(j["cost"])}
            for j in sorted(judges, key=lambda j: -j["f1"])
        ],
        "cost": dollars(sum(j["cost"] for j in judges)),
    }


def cost(log: EvalLog, prices: dict, ids: set[str] | None = None, reused: bool = False) -> float:
    """Dollars, summed call by call from the model events in the log and prices.toml.

    Call by call, because a judge that grades a finished log (Gemma's run) is recorded in its events
    but not in the log's totals. A call Inspect answered from its own cache was paid for by an earlier
    run: with reused=False it costs nothing here (what this run spent), and with reused=True it is
    priced like any other call (what the log's results cost, each call once). A call sent through a
    Batch API costs half. Inspect reports uncached input, cache writes and cache reads as separate
    counts (Opus's trial: 1,114 input beside 1,944 written), each priced at its own rate."""
    total = 0.0
    for sample in log.samples or []:
        if ids is not None and str(sample.id) not in ids:
            continue
        for event in sample.events:
            if event.event != "model" or not event.output or not event.output.usage:
                continue
            if event.cache == "read" and not reused:
                continue
            if event.model not in prices["models"]:
                raise KeyError(f"prices.toml has no price for {event.model}")
            price, usage = prices["models"][event.model], event.output.usage
            written, read = usage.input_tokens_cache_write or 0, usage.input_tokens_cache_read or 0
            dollars = (
                usage.input_tokens * price["input"]
                + written * price.get("cache_write", price["input"])
                + read * price.get("cache_read", price["input"])
                + usage.output_tokens * price["output"]
            ) / 1e6
            total += dollars * (prices["batch_discount"] if event.config and event.config.batch else 1.0)
    return total


def points(x: float) -> str:
    """A score from 0 to 1 as points out of 100, to one decimal."""
    return f"{100 * x:.1f}"


def signed(x: float) -> str:
    return f"{100 * x:+.1f}".replace("-", "−")


def estimate(e: stats.Estimate, sign: bool = False) -> dict[str, str]:
    show = signed if sign else points
    return {
        "n": f"{e.n:,}",
        "score": show(e.mean),
        "size": f"{100 * abs(e.mean):.1f}",  # unsigned, for a sentence that already says which way
        "se": f"{100 * e.se:.1f}",
        "low": show(e.low),
        "high": show(e.high),
    }


def dollars(x: float) -> str:
    return f"${x:,.2f}"


def date(log: EvalLog) -> str:
    return log.stats.started_at[:10] if log.stats.started_at else ""


def subset(values: dict[str, float], ids: set[str]) -> dict[str, float]:
    return {k: v for k, v in values.items() if k in ids}


def compute(logs: Path, prices_path: Path, trial: Path | None = None) -> dict:
    paths = find_logs(logs)
    prices = tomllib.loads(prices_path.read_text())
    check = read_check_run(paths["check"])
    models = {run: read_model_run(paths[run]) for run in ("opus", "gemma")}
    opus, gemma = models["opus"], models["gemma"]
    if opus.group != gemma.group:
        raise ValueError("the two runs disagree on which conversations are in which user group")

    ids = {"all": set(opus.score)}
    for name in GROUPS:
        ids[name] = {k for k, g in opus.group.items() if g == name}

    results: dict = {"models": {}, "difference": {}, "groups": {}, "axes": {}, "judge": {}, "cost": {}, "runs": {}}
    for run, m in models.items():
        results["models"][run] = {
            "name": NAMES[run],
            "all": estimate(stats.mean_se(m.score.values())),
            "adjusted": estimate(stats.mean_se(m.adjusted.values())),
            "length": f"{round(sum(m.length.values()) / len(m.length)):,}",
            "capped": f"{m.capped:,}",
            "filtered": f"{m.filtered:,}",
            "bootstrap": {
                "score": points(m.log.results.scores[0].metrics["bootstrap_score"].value),
                "se": f"{100 * m.log.results.scores[0].metrics['std'].value:.1f}",
            },
            "judge_failed": f"{m.judge_failed:,}",
            "criteria": f"{m.criteria:,}",
            "shared": f"{m.shared:,}",
            "own": f"{m.criteria - m.shared:,}",
            "versions": ", ".join(sorted(m.versions)),
        }
        for name in GROUPS:
            results["models"][run][name] = estimate(stats.mean_se(subset(m.score, ids[name]).values()))
    results["difference"]["all"] = estimate(stats.paired(opus.score, gemma.score), sign=True)
    results["difference"]["adjusted"] = estimate(stats.paired(opus.adjusted, gemma.adjusted), sign=True)
    for name in GROUPS:
        results["difference"][name] = estimate(
            stats.paired(subset(opus.score, ids[name]), subset(gemma.score, ids[name])), sign=True
        )
        results["groups"][name] = {"n": f"{len(ids[name]):,}", "name": GROUP_NAMES[name]}
    for axis in AXES:
        shared = set(opus.axis[axis]) & set(gemma.axis[axis])
        if len(shared) < 2:
            continue  # too few conversations carry this axis for a standard error
        results["axes"][axis] = {
            "name": AXIS_NAMES[axis],
            "n": f"{len(shared):,}",
            "opus": estimate(stats.mean_se(subset(opus.axis[axis], shared).values())),
            "gemma": estimate(stats.mean_se(subset(gemma.axis[axis], shared).values())),
            "difference": estimate(stats.paired(subset(opus.axis[axis], shared), subset(gemma.axis[axis], shared)), sign=True),
        }

    physicians = {g: stats.physician_agreement(items) for g, items in check.items.items()}
    bar = physicians["all"].percentile(BAR_QUANTILE)
    judge_all = stats.judge_f1(check.items["all"])
    results["judge"] = {
        "model": judge_of(check.log).split("/", 1)[1],
        "versions": ", ".join(sorted(check.judge_versions | opus.judge_versions | gemma.judge_versions)),
        "rows": f"{len(check.items['all']):,}",
        "f1": f"{judge_all:.3f}",
        "majority_f1": f"{check.inspect_majority_f1:.3f}",
        "physicians": f"{len(physicians['all'].scores):,}",
        "physician_weighted": f"{physicians['all'].weighted:.3f}",
        "physician_median": f"{bar:.3f}",
        "physician_third": f"{physicians['all'].percentile(1 / 3):.3f}",
        "clears_bar": "yes" if judge_all >= bar else "no",
        "failed": f"{check.judge_failed:,}",
        "groups": {
            g: {
                "rows": f"{len(check.items[g]):,}",
                "f1": f"{stats.judge_f1(check.items[g]):.3f}",
                "physician_weighted": f"{physicians[g].weighted:.3f}",
                "physician_median": f"{physicians[g].percentile(BAR_QUANTILE):.3f}",
            }
            for g in GROUPS
        },
    }

    # What the results cost: every call in the three runs once, including those a run reused from
    # Inspect's cache after an earlier run paid for them (the Opus run was restarted once).
    runs = (("check", check), ("opus", opus), ("gemma", gemma))
    costs = {run: cost(m.log, prices, reused=True) for run, m in runs}
    results["cost"] = {run: dollars(c) for run, c in costs.items()} | {"total": dollars(sum(costs.values()))}
    # The check reused the trial's GPT-4.1 grading of its rows, so the trial's cost overlaps by this.
    results["cost"]["check_reused"] = dollars(costs["check"] - cost(check.log, prices))
    results["prices_read"] = str(prices["read"])
    if trial is not None:
        results["trial"] = read_trial(trial, prices)
    for run, m in (("check", check), ("opus", opus), ("gemma", gemma)):
        usage = m.log.stats.model_usage or {}
        results["runs"][run] = {
            "date": date(m.log),
            "file": paths[run].name,
            "batch": "yes" if (m.log.eval.metadata or {}).get("batch") else "no",
            "tokens": {
                model.replace("/", "_").replace(".", "_"): {"input": f"{u.input_tokens:,}", "output": f"{u.output_tokens:,}"}
                for model, u in usage.items()
            },
        }
    results["inspect"] = opus.log.eval.packages or {}
    results["chart"] = {
        "rows": [
            (group, run, stats_for(models[run], ids[group]))
            for group in ("all", *GROUPS)
            for run in ("opus", "gemma")
        ]
    }
    return results


def stats_for(m: ModelRun, ids: set[str]) -> stats.Estimate:
    return stats.mean_se(subset(m.score, ids).values())


def tables(results: dict) -> tuple[str, str]:
    """The note's two tables, in Markdown: scores by user group, and scores by axis."""
    m, d = results["models"], results["difference"]
    lines = [
        "| Conversations | n | Claude Opus 5.5 | Gemma 3 12B | Opus minus Gemma (paired) |",
        "|:--|--:|--:|--:|--:|",
    ]
    for group in ("all", *GROUPS):
        o, g, x = m["opus"][group], m["gemma"][group], d[group]
        name = GROUP_NAMES[group]
        lines.append(
            f"| {name} | {o['n']} | {o['score']} ± {o['se']} | {g['score']} ± {g['se']} | {x['score']} ± {x['se']} |"
        )
    o, g, x = m["opus"]["adjusted"], m["gemma"]["adjusted"], d["adjusted"]
    lines.append(f"| All, length-adjusted | {o['n']} | {o['score']} ± {o['se']} | {g['score']} ± {g['se']} | {x['score']} ± {x['se']} |")
    lines += [
        "",
        ": Scores out of 100, each with its standard error. The 95% interval is the score plus or minus 1.96 standard errors. {#tbl-scores}",
        "",
    ]
    scores = "\n".join(lines)
    lines = [
        "| Axis | n | Claude Opus 5.5 | Gemma 3 12B | Opus minus Gemma (paired) |",
        "|:--|--:|--:|--:|--:|",
    ]
    for axis in AXES:
        if axis not in results["axes"]:
            continue
        a = results["axes"][axis]
        lines.append(
            f"| {a['name']} | {a['n']} | {a['opus']['score']} ± {a['opus']['se']} | "
            f"{a['gemma']['score']} ± {a['gemma']['se']} | {a['difference']['score']} ± {a['difference']['se']} |"
        )
    lines += ["", ": Scores by axis, out of 100, each with its standard error, over the conversations whose criteria include that axis. {#tbl-axes}", ""]
    return scores, "\n".join(lines)


def judges_table(trial: dict) -> str:
    lines = ["| Judge | Agreement with physicians (macro F1) | Says met | Cost |", "|:--|--:|--:|--:|"]
    lines += [f"| {j['name']} | {j['f1']} | {j['met']} | {j['cost']} |" for j in trial["judges"]]
    lines += [f"| *Physicians, against each other* | *{trial['physicians_weighted']}* | *{trial['physicians_met']}* | |"]
    lines += ["", f": Each judge on the same {trial['rows']} randomly chosen rows of the judge check (seed {trial['seed']}). The bar, fixed before any judge ran, was the median physician on all the rows. {{#tbl-judges}}", ""]
    return "\n".join(lines)


def write(results: dict, out: Path) -> None:
    rows = results.pop("chart")["rows"]
    svg = chart.error_bars(
        title="HealthBench communication theme: score by model and user group",
        description=(
            "Scores out of 100 with 95% intervals. "
            + "; ".join(
                f"{GROUP_NAMES[g]}, {NAMES[r]}: {points(e.mean)} ({points(e.low)} to {points(e.high)})" for g, r, e in rows
            )
            + "."
        ),
        groups=[GROUP_NAMES[g] for g in ("all", *GROUPS)],
        series=[chart.Series(NAMES["opus"], "opus", "circle"), chart.Series(NAMES["gemma"], "gemma", "square")],
        values={(GROUP_NAMES[g], NAMES[r]): (e.mean * 100, e.low * 100, e.high * 100) for g, r, e in rows},
        axis_label="Score out of 100",
    )
    (out / "_chart.md").write_text("```{=html}\n" + svg + "\n```\n")
    scores, axes = tables(results)
    (out / "_table-scores.md").write_text(scores)
    (out / "_table-axes.md").write_text(axes)
    if "trial" in results:
        (out / "_table-judges.md").write_text(judges_table(results["trial"]))
    # JSON is YAML, and Quarto reads it as the page's metadata.
    (out / "_results.yml").write_text(json.dumps({"r": results}, indent=1, ensure_ascii=False) + "\n")


def main() -> int:
    try:
        results = compute(HERE / "logs", HERE / "prices.toml", HERE / "judge-trial")
    except MissingLog as error:
        print(f"{HERE.name}: {error}", file=sys.stderr)
        return 1
    write(results, HERE)
    return 0


if __name__ == "__main__":
    sys.exit(main())

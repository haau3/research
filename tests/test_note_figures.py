"""A note's numbers come from its logs: recomputed by hand from the log they match, a changed log
changes them, and a missing log stops the build. Read from the stand-in logs in
tests/fixtures/healthbench/ (tests/fixtures/make_healthbench_logs.py makes them).

Needs Inspect, which the build installs:

    uv run python -m unittest discover -s tests -p test_note_figures.py
"""

from __future__ import annotations

import shutil
import statistics
import sys
import tempfile
import unittest
from math import sqrt
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NOTE = ROOT / "notes" / "healthbench-who-is-asking"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "healthbench"
sys.path.insert(0, str(NOTE))

import figures  # noqa: E402
from inspect_ai.log import read_eval_log, write_eval_log  # noqa: E402

PRICES = NOTE / "prices.toml"


def copy_fixtures(without: str | None = None) -> Path:
    out = Path(tempfile.mkdtemp())
    for path in FIXTURES.glob("*.eval"):
        run = read_eval_log(str(path), header_only=True).eval.metadata["run"]
        if run != without:
            shutil.copy(path, out / path.name)
    return out


def log_of(logs: Path, run: str) -> Path:
    return next(p for p in logs.glob("*.eval") if read_eval_log(str(p), header_only=True).eval.metadata["run"] == run)


def scores(path: Path) -> dict[str, float]:
    log = read_eval_log(str(path))
    return {str(s.id): next(iter(s.scores.values())).value["healthbench_score"] for s in log.samples}


class FromTheLogs(unittest.TestCase):
    def test_a_standard_error_recomputed_by_hand_matches(self) -> None:
        logs = copy_fixtures()
        results = figures.compute(logs, PRICES)
        opus = list(scores(log_of(logs, "opus")).values())
        by_hand = statistics.stdev(opus) / sqrt(len(opus))
        self.assertEqual(results["models"]["opus"]["all"]["se"], f"{100 * by_hand:.1f}")
        self.assertEqual(results["models"]["opus"]["all"]["score"], f"{100 * statistics.fmean(opus):.1f}")

    def test_the_paired_difference_recomputed_by_hand_matches(self) -> None:
        logs = copy_fixtures()
        results = figures.compute(logs, PRICES)
        opus, gemma = scores(log_of(logs, "opus")), scores(log_of(logs, "gemma"))
        differences = [opus[k] - gemma[k] for k in opus]
        self.assertEqual(results["difference"]["all"]["se"], f"{100 * statistics.stdev(differences) / sqrt(len(differences)):.1f}")

    def test_a_changed_log_changes_the_number(self) -> None:
        logs = copy_fixtures()
        before = figures.compute(logs, PRICES)["models"]["opus"]["all"]["score"]
        path = log_of(logs, "opus")
        log = read_eval_log(str(path))
        first = next(iter(log.samples[0].scores.values()))
        first.value["healthbench_score"] = first.value["healthbench_score"] + 0.5
        write_eval_log(log, str(path))
        after = figures.compute(logs, PRICES)["models"]["opus"]["all"]["score"]
        self.assertNotEqual(before, after)
        # The control: the other model's number did not move.
        self.assertEqual(
            figures.compute(copy_fixtures(), PRICES)["models"]["gemma"]["all"]["score"],
            figures.compute(logs, PRICES)["models"]["gemma"]["all"]["score"],
        )

    def test_a_missing_log_stops_the_build_and_names_it(self) -> None:
        for run in figures.RUNS:
            with self.subTest(run=run), self.assertRaisesRegex(figures.MissingLog, f"'{run}'"):
                figures.compute(copy_fixtures(without=run), PRICES)

    def test_two_logs_for_one_run_stop_the_build(self) -> None:
        logs = copy_fixtures()
        shutil.copy(log_of(logs, "gemma"), logs / "copy.eval")
        with self.assertRaisesRegex(figures.MissingLog, "2 logs"):
            figures.compute(logs, PRICES)

    def test_the_page_files_are_written(self) -> None:
        out = Path(tempfile.mkdtemp())
        figures.write(figures.compute(copy_fixtures(), PRICES), out)
        self.assertIn("<svg", (out / "_chart.md").read_text())
        self.assertIn("Opus minus Gemma (paired)", (out / "_table-scores.md").read_text())
        self.assertIn('"r":', (out / "_results.yml").read_text())

    def test_a_model_with_no_price_stops_the_build(self) -> None:
        prices = Path(tempfile.mkdtemp()) / "prices.toml"
        prices.write_text('read = 2026-11-25\nbatch_discount = 0.5\n[models."other/model"]\ninput = 1\noutput = 1\n')
        with self.assertRaises(KeyError):
            figures.compute(copy_fixtures(), prices)


class Cost(unittest.TestCase):
    """Cost is summed call by call: a call answered from Inspect's cache is free, a batched call costs
    half, and uncached input, cache writes, cache reads and output each have their own price."""

    def test_worked_by_hand(self) -> None:
        import tomllib

        from inspect_ai.model import GenerateConfig, ModelUsage

        log = read_eval_log(str(log_of(FIXTURES, "opus")))
        events = [e for s in log.samples for e in s.events if e.event == "model" and e.output]
        for event in events:
            event.cache = "read"
        plain, batched = events[0], events[1]
        for event in (plain, batched):
            event.cache = None
            event.model = "test/model"
        plain.output.usage = ModelUsage(
            input_tokens=1_000_000, input_tokens_cache_write=1_000_000, input_tokens_cache_read=1_000_000, output_tokens=0
        )
        batched.output.usage = ModelUsage(input_tokens=0, output_tokens=1_000_000)
        batched.config = GenerateConfig(batch=True)
        prices = tomllib.loads(
            'read = 2026-11-25\nbatch_discount = 0.5\n[models."test/model"]\n'
            "input = 2\noutput = 4\ncache_write = 3\ncache_read = 1\n"
        )
        # Plain: 2 + 3 + 1 for a million each of input, writes and reads. Batched: half of 4.
        self.assertAlmostEqual(figures.cost(log, prices), 2 + 3 + 1 + 4 * 0.5)


class Prices(unittest.TestCase):
    def test_every_price_names_its_page(self) -> None:
        import tomllib

        prices = tomllib.loads(PRICES.read_text())
        for model, price in prices["models"].items():
            with self.subTest(model=model):
                self.assertTrue(price["source"])
                self.assertGreaterEqual(price["input"], 0)
                self.assertGreaterEqual(price["output"], 0)


if __name__ == "__main__":
    unittest.main()

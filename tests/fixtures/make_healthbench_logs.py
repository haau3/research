"""Make the stand-in logs in tests/fixtures/healthbench/, which the tests of a HealthBench note read.

They come from Inspect's mockllm, which calls nothing and costs nothing: its answers are strings of
"x" of random length, and the judge's verdicts are random JSON. Only their shape is real: HealthBench's
conversations, criteria and physician grades, read through the same task and run script as the note.

    uv run --group harness python tests/fixtures/make_healthbench_logs.py
"""

import random
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NOTE = ROOT / "notes" / "healthbench-expertise-tailored"
OUT = Path(__file__).resolve().parent / "healthbench"
sys.path.insert(0, str(NOTE))

import inspect_ai  # noqa: E402
import run  # noqa: E402  (sets the dataset cache before inspect_evals loads)
from inspect_ai.model import ModelOutput, get_model  # noqa: E402
from inspect_evals.healthbench import healthbench  # noqa: E402
from inspect_evals.healthbench.dataset import load_healthbench_dataset  # noqa: E402

rng = random.Random(7)


def verdicts(n: int):
    for _ in range(n):
        met = str(rng.random() < 0.6).lower()
        yield ModelOutput.from_content("mockllm/model", f'{{"explanation": "stand-in", "criteria_met": {met}}}')


def main() -> None:
    shutil.rmtree(OUT, ignore_errors=True)
    theme = run.theme_ids()
    tags = {str(s.id): s.metadata["example_tags"] for s in load_healthbench_dataset("full")}
    # Three conversations from each user group and two from neither, so every group has a standard error.
    pick = []
    for label in ("physician_agreed_category:not-health-professional", "physician_agreed_category:health-professional"):
        pick += [i for i in theme if label in tags[i]][:3]
    pick += [i for i in theme if not any(t.startswith("physician_agreed") for t in tags[i])][:2]

    def metadata(name: str, samples: int) -> dict:
        return {"run": name, "samples": samples, "batch": False, "canary": run.CANARY, "stand_in": True}

    for name in ("opus", "gemma"):
        judge = get_model("mockllm/model", custom_outputs=list(verdicts(400)), memoize=False)
        answers = [ModelOutput.from_content("mockllm/model", "x" * rng.randint(500, 9000)) for _ in pick]
        task = healthbench(
            judge_model=judge,
            include_subset_scores=True,
            length_adjustment_center=run.LENGTH_CENTER,
            length_adjustment_penalty_per_500_chars=run.LENGTH_PENALTY_PER_500,
        )
        model = get_model("mockllm/model", custom_outputs=answers, memoize=False)
        inspect_ai.eval(task, model=model, sample_id=pick, log_dir=str(OUT), display="none", metadata=metadata(name, len(pick)))

    # Eight judge-check rows from each user group's clusters.
    rows = [s for s in load_healthbench_dataset("meta_eval") if s.metadata["category"].startswith(run.CHECK_CLUSTERS)]
    check = []
    for group in ("_not-health-professional_", "_health-professional_"):
        check += [str(s.id) for s in rows if group in s.metadata["category"]][:8]
    judge = get_model("mockllm/model", custom_outputs=list(verdicts(100)), memoize=False)
    task = healthbench(subset="meta_eval", judge_model=judge)
    inspect_ai.eval(task, model="mockllm/model", sample_id=check, log_dir=str(OUT), display="none", metadata=metadata("check", len(check)))


if __name__ == "__main__":
    main()

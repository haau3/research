"""Try candidate judges on the same rows of the judge check, before choosing one for the full check.

gpt-6-luna, at its default settings, agreed with physicians far less than the bar this note set
before any result (the median physician's macro F1). This grades one fixed random sample of the
check's rows with each candidate, into judge-trial/, so the choice rests on the same rows for all.

    uv run --group harness python notes/healthbench-expertise-tailored/judge_trial.py [--rows 600] [--only NAME]
"""

import argparse
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import inspect_ai  # noqa: E402
import run  # noqa: E402  (sets the dataset cache before inspect_evals loads)
from dotenv import load_dotenv  # noqa: E402
from inspect_ai.model import GenerateConfig, get_model  # noqa: E402
from inspect_evals.healthbench import healthbench  # noqa: E402

SEED = 20261125
CANDIDATES = {
    # The first judge, at its default settings. Its full check on every row is on the release, outside
    # the repository (its log is over the repository's 50 MB limit); these rows come from that run
    # through Inspect's cache.
    "luna-default": ("openai/gpt-6-luna", GenerateConfig()),
    # HealthBench's original judges did not reason before answering.
    "luna-no-reasoning": ("openai/gpt-6-luna", GenerateConfig(reasoning_effort="none")),
    "luna-high": ("openai/gpt-6-luna", GenerateConfig(reasoning_effort="high")),
    "haiku": ("anthropic/claude-haiku-5-5", GenerateConfig()),
    "terra": ("openai/gpt-6.1-terra", GenerateConfig()),
    # The control: the judge HealthBench's paper measured (0.610 on this theme), served until
    # 2027-01-14. Close to that means this setup is the paper's; far from it means it is not.
    "gpt-4.1": ("openai/gpt-4.1", GenerateConfig()),
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rows", type=int, default=600)
    parser.add_argument("--only", choices=sorted(CANDIDATES))
    parser.add_argument("--out", default=str(HERE / "judge-trial"))
    args = parser.parse_args()
    # The judges are made here, before eval() would read the API keys from .env, so read it first.
    load_dotenv(HERE.parents[1] / ".env")

    rows = sorted(random.Random(SEED).sample(run.check_ids(), args.rows))
    ok = True
    for name, (model, config) in CANDIDATES.items():
        if args.only and name != args.only:
            continue
        judge = get_model(model, config=config)
        task = healthbench(subset="meta_eval", judge_model=judge)
        metadata = {
            "canary": run.CANARY,
            "note": HERE.name,
            "run": f"judge-trial-{name}",
            "judge": model,
            "judge_config": config.model_dump(exclude_none=True),
            "seed": SEED,
            "samples": len(rows),
            "batch": False,
            "healthbench_sha256": run.DATA_SHA256,
        }
        logs = inspect_ai.eval(
            task,
            model="mockllm/model",
            sample_id=rows,
            metadata=metadata,
            tags=["judge-trial", name],
            log_dir=args.out,
            log_format="eval",
            max_samples=len(rows),
            retry_on_error=3,
            fail_on_error=False,
        )
        ok &= all(log.status == "success" for log in logs)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

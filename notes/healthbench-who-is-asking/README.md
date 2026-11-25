# A frontier model and a laptop model on HealthBench's who-is-asking conversations

The note is [index.qmd](index.qmd), published at
<https://research.haau3.com/notes/healthbench-who-is-asking/>. Everything it reports comes from the
three logs in [logs/](logs/).

| File | What it is |
|---|---|
| [run.py](run.py) | Runs the three evaluations with Inspect; every setting is in it |
| [figures.py](figures.py) | Computes every number on the note from the logs |
| [prices.toml](prices.toml) | Each model's price, with the page and date it was read |
| [logs/](logs/) | Inspect's logs: every conversation, answer, judgment and token count |

## Run it again

You need [uv](https://docs.astral.sh/uv/), an Anthropic API key and an OpenAI API key in a `.env` file
at the repository's root (Inspect reads it), and, for Gemma, a Mac with Apple Silicon and about
10 GB of free memory.

```sh
uv sync --group harness
```

The judge's check against physicians, which calls only the judge:

```sh
uv run --group harness python notes/healthbench-who-is-asking/run.py check
```

Claude Opus 5.5:

```sh
uv run --group harness python notes/healthbench-who-is-asking/run.py opus
```

Gemma 3 12B on the laptop. Download the pinned revision once, then start the server in its own
terminal, offline so that it can only load that revision, with Google's sampling settings and a
small prompt cache (16 GB runs out of GPU memory otherwise):

```sh
uv run --group harness python -c "from huggingface_hub import snapshot_download; print(snapshot_download('mlx-community/gemma-3-12b-it-qat-4bit', revision='main'))"
HF_HUB_OFFLINE=1 uv run --group harness mlx_lm.server --model mlx-community/gemma-3-12b-it-qat-4bit \
  --host 127.0.0.1 --port 8080 --temp 1.0 --top-p 0.95 --top-k 64 --max-tokens 16384 --prompt-cache-size 2
```

The path the first command prints must end in `66fc51ef25778c03d33c4c8bc446973d062e73f4`, the
revision this note used; run.py refuses any other. Then, in a second terminal (it takes about a day on
an M1 Pro, so keep the computer awake):

```sh
caffeinate -i uv run --group harness python notes/healthbench-who-is-asking/run.py gemma
```

The judge reads Gemma's answers afterwards, in the same log, so the laptop never waits on it (the
server can be stopped by then):

```sh
uv run --group harness python notes/healthbench-who-is-asking/run.py gemma-judge
```

Add `--smoke` to any run for a few samples into `smoke-logs/`, which is not committed.

## Read the logs

```sh
uv run --group harness inspect view --log-dir notes/healthbench-who-is-asking/logs
```

Or from Python, with Inspect's [log API](https://inspect.aisi.org.uk/eval-logs.html):
`inspect_ai.log.read_eval_log(path)`.

## Data

HealthBench is © OpenAI, under the MIT licence; its conversations and criteria appear in the logs
under that licence. Each log carries this repository's canary string in its metadata.

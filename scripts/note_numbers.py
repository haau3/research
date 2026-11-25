"""Make every note's numbers from its logs before the site renders.

The build runs this before `quarto render`, on the laptop and in CI alike (CONTRIBUTING.md). It
runs each note's figures.py, which reads that note's logs and writes
the files its page includes. A note whose logs are missing or incomplete stops the build, naming the
log, so no page can show a number its logs do not hold.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    failed = []
    for script in sorted(ROOT.glob("notes/*/figures.py")):
        if subprocess.run([sys.executable, str(script)], cwd=script.parent).returncode != 0:
            failed.append(script.parent.name)
    if failed:
        print(f"no numbers for: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

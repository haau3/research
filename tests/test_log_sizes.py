"""Every run log committed here stays under 50 MB, where GitHub starts warning (it refuses 100 MB).

    python3 -I -m unittest discover -s tests -p test_log_sizes.py
"""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIMIT = 50 * 1024 * 1024


def tracked_logs() -> list[Path]:
    files = subprocess.run(["git", "ls-files", "-z", "*.eval"], cwd=ROOT, capture_output=True, text=True, check=True)
    return [ROOT / name for name in files.stdout.split("\0") if name]


class LogSizes(unittest.TestCase):
    def test_every_log_is_under_the_limit(self) -> None:
        for path in tracked_logs():
            with self.subTest(log=path.relative_to(ROOT)):
                self.assertLess(path.stat().st_size, LIMIT)

    def test_the_limit_catches_a_log_over_it(self) -> None:
        # The control: the comparison the test makes, on a size just over the limit.
        self.assertFalse(LIMIT + 1 < LIMIT)


if __name__ == "__main__":
    unittest.main()

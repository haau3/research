"""scripts/stats.py against examples worked by hand.

    python3 -I -m unittest discover -s tests -p test_stats.py
"""

from __future__ import annotations

import sys
import unittest
from math import sqrt
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from stats import Z95, Grades, judge_f1, macro_f1, mean_se, paired, physician_agreement  # noqa: E402


class MeanAndStandardError(unittest.TestCase):
    def test_worked_by_hand(self) -> None:
        # Mean 2.5; deviations 1.5, 0.5, 0.5, 1.5; variance (2.25 + 0.25 + 0.25 + 2.25) / 3 = 5/3;
        # standard error sqrt(5/3) / sqrt(4).
        e = mean_se([1, 2, 3, 4])
        self.assertEqual(e.n, 4)
        self.assertAlmostEqual(e.mean, 2.5)
        self.assertAlmostEqual(e.se, sqrt(5 / 3) / 2)
        self.assertAlmostEqual(e.low, 2.5 - Z95 * sqrt(5 / 3) / 2)
        self.assertAlmostEqual(e.high, 2.5 + Z95 * sqrt(5 / 3) / 2)

    def test_one_value_has_no_standard_error(self) -> None:
        with self.assertRaises(ValueError):
            mean_se([0.5])


class Paired(unittest.TestCase):
    def test_worked_by_hand(self) -> None:
        # Differences 1, 2, 0: mean 1, standard deviation 1, standard error 1 / sqrt(3).
        e = paired({"a": 1.0, "b": 3.0, "c": 2.0}, {"a": 0.0, "b": 1.0, "c": 2.0})
        self.assertAlmostEqual(e.mean, 1.0)
        self.assertAlmostEqual(e.se, 1 / sqrt(3))

    def test_smaller_than_unpaired_when_the_questions_share_variance(self) -> None:
        # The same hard and easy questions for both models: pairing removes what they share.
        a = {"q1": 0.9, "q2": 0.2, "q3": 0.7, "q4": 0.4}
        b = {"q1": 0.8, "q2": 0.1, "q3": 0.5, "q4": 0.3}
        unpaired = sqrt(mean_se(a.values()).se ** 2 + mean_se(b.values()).se ** 2)
        self.assertLess(paired(a, b).se, unpaired)

    def test_a_question_one_model_lacks_is_an_error_not_dropped(self) -> None:
        with self.assertRaises(ValueError):
            paired({"a": 1.0, "b": 2.0}, {"a": 1.0})


class Agreement(unittest.TestCase):
    def test_macro_f1_worked_by_hand(self) -> None:
        # Met: 1 true positive, 0 false positives, 1 false negative: 2/3.
        # Not met: 2 true positives, 1 false positive, 0 false negatives: 4/5. Mean 11/15.
        self.assertAlmostEqual(macro_f1([True, True, False, False], [True, False, False, False]), 11 / 15)

    def test_macro_f1_of_a_class_neither_side_uses_is_zero(self) -> None:
        self.assertAlmostEqual(macro_f1([True, True], [True, True]), 0.5)

    def test_the_judge_against_each_physicians_grade(self) -> None:
        # Comparisons: (met, met) (not, met) (met, not) (met, not), as (physician, judge).
        # Met: 1 true positive, 1 false positive, 2 false negatives: 2/5. Not met: 0. Mean 1/5.
        items = [Grades(["p1", "p2"], [True, False], True), Grades(["p1", "p3"], [True, True], False)]
        self.assertAlmostEqual(judge_f1(items), 0.2)

    def test_each_physician_against_the_others_never_themselves(self) -> None:
        # p1 and p2 on three items: (met, met) (met, not) (not, not). Each scores 2/3 against the other.
        items = [
            Grades(["p1", "p2"], [True, True]),
            Grades(["p1", "p2"], [True, False]),
            Grades(["p1", "p2"], [False, False]),
        ]
        physicians = physician_agreement(items)
        self.assertAlmostEqual(physicians.scores["p1"], 2 / 3)
        self.assertAlmostEqual(physicians.scores["p2"], 2 / 3)
        self.assertEqual(physicians.examples, {"p1": 3, "p2": 3})
        self.assertAlmostEqual(physicians.weighted, 2 / 3)

    def test_the_average_is_weighted_by_items_graded(self) -> None:
        # p3 graded one item and agreed with p1 on it: 1.0 on 1 item, against p1's 3 and p2's 2.
        items = [
            Grades(["p1", "p2"], [True, True]),
            Grades(["p1", "p2"], [True, False]),
            Grades(["p1", "p3"], [False, False]),
        ]
        physicians = physician_agreement(items)
        weights = physicians.examples
        expected = sum(physicians.scores[p] * weights[p] for p in weights) / sum(weights.values())
        self.assertAlmostEqual(physicians.weighted, expected)
        self.assertEqual(weights, {"p1": 3, "p2": 2, "p3": 1})

    def test_percentile_interpolates_between_ranks(self) -> None:
        physicians = physician_agreement(
            [Grades(["a", "b"], [True, True]), Grades(["a", "b"], [False, False]), Grades(["a", "c"], [True, False])]
        )
        ranked = sorted(physicians.scores.values())
        self.assertAlmostEqual(physicians.percentile(0.5), ranked[1])
        self.assertAlmostEqual(physicians.percentile(0.25), ranked[0] + (ranked[1] - ranked[0]) / 2)


if __name__ == "__main__":
    unittest.main()

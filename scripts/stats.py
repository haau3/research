"""The statistics the notes use, with the standard library only.

A score over questions is a mean, and its standard error comes from the central limit theorem: the
sample standard deviation over the square root of the number of questions. Two models that answered
the same questions are compared question by question, which removes the part of the variance the
questions share. Both follow Miller, "Adding Error Bars to Evals: A Statistical Approach to Language
Model Evaluations" (arXiv 2411.00640).

Agreement between graders is HealthBench's macro F1: the F1 score of "met" and of "not met",
averaged, so that the rarer answer counts as much as the common one (arXiv 2505.08775, section 6).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from math import sqrt
from statistics import fmean, stdev

# The two-sided 95% point of the standard normal distribution.
Z95 = 1.959963984540054


@dataclass(frozen=True)
class Estimate:
    """A mean over n questions and its standard error."""

    n: int
    mean: float
    se: float

    @property
    def low(self) -> float:
        return self.mean - Z95 * self.se

    @property
    def high(self) -> float:
        return self.mean + Z95 * self.se


def mean_se(values: Iterable[float]) -> Estimate:
    """The mean of the values and its central-limit standard error."""
    values = list(values)
    if len(values) < 2:
        raise ValueError(f"a standard error needs at least 2 values, got {len(values)}")
    return Estimate(len(values), fmean(values), stdev(values) / sqrt(len(values)))


def paired(a: Mapping[str, float], b: Mapping[str, float]) -> Estimate:
    """a minus b, question by question, over questions both answered. Fails if either has one the
    other does not, because a comparison that quietly drops questions is not paired."""
    if a.keys() != b.keys():
        only = sorted(a.keys() ^ b.keys())
        raise ValueError(f"{len(only)} questions are scored for one model only, for example {only[:3]}")
    return mean_se(a[k] - b[k] for k in sorted(a))


def macro_f1(truth: Sequence[bool], prediction: Sequence[bool]) -> float:
    """The mean of the F1 scores of True and of False. A class that appears in neither scores 0, as
    scikit-learn's `f1_score(average="macro", labels=[False, True])` does."""
    if len(truth) != len(prediction):
        raise ValueError("truth and prediction differ in length")
    total = 0.0
    for label in (True, False):
        tp = sum(t == label and p == label for t, p in zip(truth, prediction))
        fp = sum(t != label and p == label for t, p in zip(truth, prediction))
        fn = sum(t == label and p != label for t, p in zip(truth, prediction))
        total += 2 * tp / (2 * tp + fp + fn) if tp or fp or fn else 0.0
    return total / 2


@dataclass(frozen=True)
class Grades:
    """One graded item: the physicians who graded it and their grades, and a model's grade."""

    physicians: Sequence[str]
    grades: Sequence[bool]
    model: bool | None = None


def judge_f1(items: Iterable[Grades]) -> float:
    """The model's macro F1 against every physician's grade, each grade one comparison, as
    HealthBench scores its grader."""
    truth: list[bool] = []
    prediction: list[bool] = []
    for item in items:
        if item.model is None:
            raise ValueError("an item has no model grade")
        truth += item.grades
        prediction += [item.model] * len(item.grades)
    return macro_f1(truth, prediction)


@dataclass(frozen=True)
class Physicians:
    """Each physician's macro F1 against the other physicians who graded the same items."""

    scores: Mapping[str, float]
    examples: Mapping[str, int]

    @property
    def weighted(self) -> float:
        """The average across physicians, weighted by how many items each one graded."""
        weight = sum(self.examples[p] for p in self.scores)
        return sum(self.scores[p] * self.examples[p] for p in self.scores) / weight

    def percentile(self, q: float) -> float:
        """The physician score at quantile q (0 to 1), by linear interpolation between ranks."""
        ranked = sorted(self.scores.values())
        position = q * (len(ranked) - 1)
        below = int(position)
        above = min(below + 1, len(ranked) - 1)
        return ranked[below] + (ranked[above] - ranked[below]) * (position - below)


def physician_agreement(items: Iterable[Grades]) -> Physicians:
    """Score each physician as the model is scored: their grade against each other physician's
    grade on the same item, never against their own."""
    truth: dict[str, list[bool]] = defaultdict(list)
    prediction: dict[str, list[bool]] = defaultdict(list)
    examples: dict[str, int] = defaultdict(int)
    for item in items:
        pairs = list(zip(item.physicians, item.grades))
        for physician, grade in pairs:
            examples[physician] += 1
            for other, other_grade in pairs:
                if other != physician:
                    truth[physician].append(other_grade)
                    prediction[physician].append(grade)
    scores = {p: macro_f1(truth[p], prediction[p]) for p in truth if truth[p]}
    return Physicians(scores, {p: examples[p] for p in scores})

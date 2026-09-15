"""Fit a first-order rate constant (a synthetic example for the visual comparison)."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Point:
    time_s: float
    concentration: float


def rate_constant(points: list[Point]) -> float:
    # ln(c) = ln(c0) - k t, fitted by least squares
    xs = [p.time_s for p in points]
    ys = [math.log(p.concentration) for p in points]
    n = len(points)
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / sum((x - mean_x) ** 2 for x in xs)
    return -slope


if __name__ == "__main__":
    data = [Point(t, math.exp(-0.05 * t)) for t in range(0, 100, 10)]
    print(f"k = {rate_constant(data):.4f} 1/s")  # a comment long enough to wrap: xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

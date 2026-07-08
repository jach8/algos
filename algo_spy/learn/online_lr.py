"""Hand-rolled online logistic regression with running feature standardization.

Prequential use: call predict_proba(x) (uses only past data), then update(x, y).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class _RunningStandardizer:
    """Welford running mean/variance for z-scoring features online."""

    n: int = 0
    mean: list[float] = field(default_factory=list)
    m2: list[float] = field(default_factory=list)

    def _ensure(self, k: int) -> None:
        if not self.mean:
            self.mean = [0.0] * k
            self.m2 = [0.0] * k

    def transform(self, x: list[float]) -> list[float]:
        self._ensure(len(x))
        out: list[float] = []
        for i, xi in enumerate(x):
            var = self.m2[i] / self.n if self.n > 1 else 1.0
            std = math.sqrt(var) if var > 1e-12 else 1.0
            out.append((xi - self.mean[i]) / std)
        return out

    def observe(self, x: list[float]) -> None:
        self._ensure(len(x))
        self.n += 1
        for i, xi in enumerate(x):
            delta = xi - self.mean[i]
            self.mean[i] += delta / self.n
            self.m2[i] += delta * (xi - self.mean[i])


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    ez = math.exp(z)
    return ez / (1.0 + ez)


@dataclass
class OnlineLogisticRegression:
    """SGD logistic regression updated one sample at a time."""

    n_features: int
    lr: float = 0.05
    l2: float = 1e-4
    weights: list[float] = field(default_factory=list)
    bias: float = 0.0
    scaler: _RunningStandardizer = field(default_factory=_RunningStandardizer)

    def __post_init__(self) -> None:
        if not self.weights:
            self.weights = [0.0] * self.n_features

    def _raw_proba(self, xz: list[float]) -> float:
        z = self.bias + sum(w * xi for w, xi in zip(self.weights, xz))
        return _sigmoid(z)

    def predict_proba(self, x: list[float]) -> float:
        return self._raw_proba(self.scaler.transform(x))

    def update(self, x: list[float], y: int) -> None:
        self.scaler.observe(x)
        xz = self.scaler.transform(x)
        p = self._raw_proba(xz)
        err = p - y
        for i, xi in enumerate(xz):
            grad = err * xi + self.l2 * self.weights[i]
            self.weights[i] -= self.lr * grad
        self.bias -= self.lr * err

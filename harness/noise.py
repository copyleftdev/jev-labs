"""Empirical noise-floor calibration.

Antithesis gets reproducibility by controlling the scheduler. We cannot control
Jev's, so we do the next best thing: measure how much the system varies when
NOTHING has changed, and treat that as the resolution limit of every subsequent
measurement.

This is the load-bearing idea of the whole harness. Without it, every reported
violation is indistinguishable from the +/-0.01 jitter we already know exists,
and the report is noise with a severity label attached.

We measure three floors, because they are not the same number:

  identity   -- the same request re-sent verbatim. Pure sampling noise.
  reorder    -- the same questions sent in a different dict order. Should be
                zero by the API's own contract (questions are independent).
  cohort     -- the same question asked about semantically identical restatements.
                This is the floor that matters for metamorphic properties, and
                it is necessarily larger than the identity floor.

A property is judged against whichever floor matches the transform it applies.
Judging a paraphrase property against the identity floor would manufacture
false positives; that mistake is the usual reason ML test harnesses get ignored.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Any

from typesafe_sdk import Noul


@dataclass
class NoiseFloor:
    """Measured variation of the system under a no-op transform."""

    kind: str
    samples: list[float]
    n_calls: int

    @property
    def mean_abs(self) -> float:
        return statistics.mean(abs(s) for s in self.samples) if self.samples else 0.0

    @property
    def max_abs(self) -> float:
        return max((abs(s) for s in self.samples), default=0.0)

    @property
    def stdev(self) -> float:
        return statistics.pstdev(self.samples) if len(self.samples) > 1 else 0.0

    @property
    def threshold(self) -> float:
        """The value an effect must exceed to be considered real.

        max observed no-op deviation, plus three standard deviations, with an
        absolute floor so that a suspiciously quiet calibration run cannot make
        the harness hypersensitive.
        """
        return max(self.max_abs + 3 * self.stdev, 0.02)

    def margin(self, effect: float) -> float:
        """How many threshold-units an effect exceeds the floor by."""
        t = self.threshold
        return round(abs(effect) / t, 3) if t > 0 else float("inf")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "n_calls": self.n_calls,
            "n_samples": len(self.samples),
            "mean_abs_deviation": round(self.mean_abs, 5),
            "max_abs_deviation": round(self.max_abs, 5),
            "stdev": round(self.stdev, 5),
            "threshold": round(self.threshold, 5),
        }


@dataclass
class NoiseProfile:
    """All measured floors for one harness session."""

    floors: dict[str, NoiseFloor] = field(default_factory=dict)

    def get(self, kind: str) -> NoiseFloor:
        if kind in self.floors:
            return self.floors[kind]
        # Conservative fallback: never gate looser than the strictest floor.
        if self.floors:
            return max(self.floors.values(), key=lambda f: f.threshold)
        return NoiseFloor(kind="fallback", samples=[0.02], n_calls=0)

    def to_dict(self) -> dict[str, Any]:
        return {k: v.to_dict() for k, v in self.floors.items()}


# ---------------------------------------------------------------------------
# Calibration probes
# ---------------------------------------------------------------------------

CALIB_TEXTS = [
    "The customer was charged twice for order A-104 and wants the duplicate refunded.",
    "Our API integration returns a 500 error on every request since yesterday.",
    "I would like to upgrade my plan to the enterprise tier before the quarter ends.",
    "The package arrived damaged and I need a replacement shipped urgently.",
]

CALIB_QUESTIONS = {
    "billing": Noul(instructions="Is this message about a billing or payment matter?"),
    "technical": Noul(instructions="Does this message report a technical malfunction?"),
    "urgent": Noul(instructions="Does this message convey urgency?"),
}


def _nouls(resp: Any) -> dict[str, float]:
    return {k: v.noul for k, v in resp.nouls.items()}


def calibrate(client: Any, repeats: int = 4) -> NoiseProfile:
    """Measure the system's no-op variation before testing anything.

    `client` must be a forensics-wrapped client so calibration itself is
    captured -- the noise floor is evidence and belongs in the record.
    """
    profile = NoiseProfile()
    calls = 0

    # --- identity floor: byte-identical request, repeated -------------------
    identity: list[float] = []
    for text in CALIB_TEXTS:
        baseline: dict[str, float] | None = None
        for _ in range(repeats):
            resp = client.system_one(text, CALIB_QUESTIONS)
            calls += 1
            vals = _nouls(resp)
            if baseline is None:
                baseline = vals
            else:
                identity.extend(vals[k] - baseline[k] for k in vals)
    profile.floors["identity"] = NoiseFloor("identity", identity, calls)

    # --- reorder floor: same questions, different insertion order ----------
    reorder: list[float] = []
    calls_r = 0
    keys = list(CALIB_QUESTIONS)
    for text in CALIB_TEXTS:
        forward = client.system_one(text, {k: CALIB_QUESTIONS[k] for k in keys})
        backward = client.system_one(
            text, {k: CALIB_QUESTIONS[k] for k in reversed(keys)}
        )
        calls_r += 2
        a, b = _nouls(forward), _nouls(backward)
        reorder.extend(b[k] - a[k] for k in a)
    profile.floors["reorder"] = NoiseFloor("reorder", reorder, calls_r)

    # --- cohort floor: semantically identical restatements -----------------
    # Trivial, meaning-preserving edits: whitespace and terminal punctuation.
    cohort: list[float] = []
    calls_c = 0
    for text in CALIB_TEXTS:
        base = client.system_one(text, CALIB_QUESTIONS)
        variants = [text + " ", " " + text, text.replace(". ", ".  ")]
        calls_c += 1
        bvals = _nouls(base)
        for v in variants:
            resp = client.system_one(v, CALIB_QUESTIONS)
            calls_c += 1
            vvals = _nouls(resp)
            cohort.extend(vvals[k] - bvals[k] for k in bvals)
    profile.floors["cohort"] = NoiseFloor("cohort", cohort, calls_c)

    return profile

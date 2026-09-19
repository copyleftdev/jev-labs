"""Core types for the Jev property-testing harness.

The central design problem: Antithesis-style verification assumes a
deterministic system under test. Jev is not deterministic -- measured jitter is
roughly +/-0.01 on repeated identical requests. A naive property checker
therefore reports noise as failure and is worthless.

The fix is to replace the boolean oracle with a *statistical* one. Every
property declares an effect it expects to be zero (or bounded), the harness
measures the system's own noise floor empirically at runtime, and a violation
is only reported when the observed effect exceeds that floor by a margin that
cannot plausibly be noise.

That is what produces a "why" rather than a "what": every finding carries the
invariant that broke, the minimal input that breaks it, the effect size, the
measured noise floor it was judged against, and the verbatim request/response
bytes from the forensic store.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Sequence


class Severity(str, Enum):
    """How much a violation should alarm the reader."""

    HARD = "hard"          # A structural guarantee broke. Always a defect.
    SEMANTIC = "semantic"  # A meaning-preserving transform changed the answer.
    CALIBRATION = "calibration"  # Probabilities are internally inconsistent.
    DOCUMENTED = "documented"    # Known/published jaggedness; measured, not news.


@dataclass(frozen=True)
class Case:
    """One concrete, reproducible test input.

    `seed` and `params` are sufficient to regenerate this case exactly, which
    is the part of determinism we CAN keep: the input side is fully replayable
    even though the model's response is not.
    """

    property_name: str
    seed: int
    params: dict[str, Any]
    label: str = ""

    @property
    def case_id(self) -> str:
        payload = json.dumps(
            {"p": self.property_name, "s": self.seed, "k": self.params},
            sort_keys=True, separators=(",", ":"), default=str,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def replay_command(self, module: str = "harness.runner") -> str:
        return (
            f"./.venv/bin/python -m {module} --replay "
            f"{self.property_name}:{self.seed}"
        )


@dataclass
class Measurement:
    """The outcome of evaluating one case.

    `effect` is the property-specific quantity that SHOULD be zero (or within
    `tolerance`). Keeping it a continuous number rather than a boolean is what
    makes shrinking and statistical gating possible -- you can compare two
    failures and say which is worse.
    """

    case: Case
    effect: float
    detail: dict[str, Any] = field(default_factory=dict)
    call_ids: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class Violation:
    """A property failure that survived statistical gating and minimization."""

    property_name: str
    hypothesis: str
    why_it_matters: str
    severity: Severity
    case: Case
    effect: float
    noise_floor: float
    margin: float              # how many noise-floor units the effect exceeds
    n_confirmations: int       # times the failure reproduced on re-run
    n_attempts: int
    shrunk_from: str | None
    shrink_steps: int
    detail: dict[str, Any]
    call_ids: list[str]

    @property
    def reproducibility(self) -> float:
        return round(self.n_confirmations / self.n_attempts, 3) if self.n_attempts else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "property": self.property_name,
            "hypothesis": self.hypothesis,
            "why_it_matters": self.why_it_matters,
            "severity": self.severity.value,
            "case_id": self.case.case_id,
            "seed": self.case.seed,
            "label": self.case.label,
            "params": self.case.params,
            "effect": round(self.effect, 6),
            "noise_floor": round(self.noise_floor, 6),
            "margin_over_noise": round(self.margin, 2),
            "reproducibility": self.reproducibility,
            "confirmations": f"{self.n_confirmations}/{self.n_attempts}",
            "shrunk_from": self.shrunk_from,
            "shrink_steps": self.shrink_steps,
            "replay": self.case.replay_command(),
            "detail": self.detail,
            "forensic_call_ids": self.call_ids,
        }


@dataclass
class Property:
    """A metamorphic relation the system under test must satisfy.

    A property is NOT an example-based assertion about a specific answer. It is
    a relation between the answers to two or more requests, which must hold
    whatever the model actually believes. That is what lets us test a system
    whose ground truth we do not own.
    """

    name: str
    hypothesis: str        # the invariant, stated precisely
    why_it_matters: str    # the consequence for someone building on the API
    severity: Severity
    generate: Callable[[int], Case]
    evaluate: Callable[[Any, Case], Measurement]
    shrink: Callable[[Case], Sequence[Case]] | None = None
    # Effects below this are never interesting even if statistically clean.
    floor_override: float | None = None

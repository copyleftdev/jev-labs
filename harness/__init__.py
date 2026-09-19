"""Metamorphic property-testing harness for TypeSafe's Jev model.

Antithesis-inspired, adapted for a non-deterministic system under test:
empirical noise-floor calibration replaces scheduler determinism, and
statistical gating plus confirmation replaces exact reproduction.
"""

from .core import Case, Measurement, Property, Severity, Violation
from .noise import NoiseFloor, NoiseProfile, calibrate
from .properties import BY_NAME, PROPERTIES

__all__ = [
    "Case", "Measurement", "Property", "Severity", "Violation",
    "NoiseFloor", "NoiseProfile", "calibrate",
    "PROPERTIES", "BY_NAME",
]

"""Smoke test for the TypeSafe System One API (jev).

Verifies: auth, model listing, and one request mixing all three primitives.
Requires TYPESAFE_API_KEY in the environment.

    ./.venv/bin/python smoke_test.py
"""

from __future__ import annotations

import os
import sys
import time

from typesafe_sdk import Choice, Noul, Score, TypeSafeClient, TypeSafeError

TICKET = (
    "Hi, I've been trying to connect my Stripe account for 3 days and it keeps "
    "failing. I'm losing sales. Please help ASAP."
)


def main() -> int:
    if not os.environ.get("TYPESAFE_API_KEY"):
        print("TYPESAFE_API_KEY is not set", file=sys.stderr)
        return 2

    with TypeSafeClient() as client:
        models = client.models.list().models
        print("models:")
        for m in models:
            print(f"  {m.name}  {m.release_date}  {m.description}")

        start = time.perf_counter()
        response = client.system_one(
            state={"ticket": TICKET},
            questions={
                "department": Choice(
                    instructions="Which team should handle this ticket?",
                    criteria={
                        "billing": "Payment, invoicing, or subscription issues",
                        "technical": "Bugs, outages, or integration problems",
                        "sales": "Pricing, upgrades, or new accounts",
                    },
                ),
                "frustration": Score(
                    instructions="How frustrated the customer appears",
                    criteria=[
                        "Calm, just stating facts",
                        "Frustrated but civil",
                        "Very angry, strong language",
                    ],
                ),
                "is_urgent": Noul(
                    instructions="The message conveys urgency or time-sensitivity",
                ),
            },
        )
        elapsed_ms = (time.perf_counter() - start) * 1000

    dept = response.choices["department"]
    frust = response.scores["frustration"]
    urgent = response.nouls["is_urgent"]

    print(f"\nmodel: {response.model}   latency: {elapsed_ms:.0f} ms")
    print(f"department:  {dept.choice}  conf={dept.confidence:.3f}  {dept.probabilities}")
    print(f"frustration: {frust.score:.3f}  conf={frust.confidence:.3f}  {frust.probabilities}")
    print(f"is_urgent:   {urgent.noul:.3f}")
    print(f"usage: {response.usage}")

    assert dept.choice in {"billing", "technical", "sales"}
    assert abs(sum(dept.probabilities.values()) - 1.0) < 1e-3
    assert 0.0 <= frust.score <= 2.0
    assert 0.0 <= urgent.noul <= 1.0
    print("\nOK")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except TypeSafeError as exc:
        print(f"TypeSafe error: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)

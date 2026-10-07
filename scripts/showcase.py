#!/usr/bin/env python3
"""Run a batch of questions through the resolver and print the readings.

Useful both as a demo and as a quick way to eyeball answer quality.

    python3 scripts/showcase.py
    python3 scripts/showcase.py --limit 4
"""

from __future__ import annotations

import argparse
import pathlib
import statistics
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from ask import render  # noqa: E402
from jev_kjv.resolver import Resolver  # noqa: E402

QUESTIONS = [
    "Why do bad things happen to good people?",
    "What happens after I die?",
    "Is it wrong to eat meat?",
    "Should I forgive someone who hurt me?",
    "How should I treat my neighbour?",
    "Does God care about the poor?",
    "Is it wrong to be rich?",
    "What should I do when I am afraid?",
    "How do I know I am loved?",
    "Is war ever justified?",
    "What is the meaning of work?",
    "Am I allowed to be angry?",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=len(QUESTIONS))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    resolver = Resolver()
    readings = []
    confidences = []
    costs = []
    latencies = []

    for question in QUESTIONS[: args.limit]:
        reading = resolver.resolve(question)
        readings.append(reading)
        confidences.append(reading.confidence)
        costs.append(reading.total_cost_usd)
        if not args.json:
            print(render(reading, colour=sys.stdout.isatty()))
        latencies.append(reading.total_latency_ms)

    print("=" * 72)
    print(f"{len(readings)} questions")
    print(f"  total cost      ${sum(costs):.6f}")
    print(f"  mean cost       ${statistics.mean(costs):.6f} per question")
    print(f"  mean latency    {statistics.mean(latencies):.0f} ms")
    print(f"  confidence      min {min(confidences):.4f}  "
          f"median {statistics.median(confidences):.4f}  max {max(confidences):.4f}")
    print("  readings")
    for reading in readings:
        print(f"    {reading.reference:<24} {reading.confidence:.4f}  {reading.question}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Is the mapping a hash, or an oracle?

A key-value store needs the same key to return the same value. Jev returns a
probability distribution, so the risk is that low-confidence levels flip
between runs and the "hash" is really a fresh roll each time.

This runs each question several times with the cache disabled and reports how
often the chain lands on exactly the same span, plus how often each level
agrees with itself. It also reports what chance would look like.

Run: python3 scripts/experiment_stability.py --runs 3
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from jev_kjv.resolver import Resolver  # noqa: E402

QUESTIONS = [
    "Why do bad things happen to good people?",
    "What happens after I die?",
    "Is it wrong to eat meat?",
    "Should I forgive someone who hurt me?",
    "How should I treat my neighbour?",
    "Does God care about the poor?",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--all", action="store_true", help="use the full question set")
    args = parser.parse_args(argv)

    questions = QUESTIONS
    resolver = Resolver(use_cache=False)

    print(f"{len(questions)} questions x {args.runs} runs, cache disabled\n")

    span_stable = 0
    level_agreement: dict[str, list[float]] = collections.defaultdict(list)
    records = []

    for question in questions:
        readings = [resolver.resolve(question) for _ in range(args.runs)]
        spans = [r.reference for r in readings]
        stable = len(set(spans)) == 1
        span_stable += stable

        per_level = {}
        for level in ("book", "chapter", "verse", "extent"):
            picks = [
                next(t.choice for t in r.traces if t.level == level) for r in readings
            ]
            counts = collections.Counter(picks)
            top_share = counts.most_common(1)[0][1] / len(picks)
            per_level[level] = {
                "picks": picks,
                "agreement": round(top_share, 3),
                "modal": counts.most_common(1)[0][0],
            }
            level_agreement[level].append(top_share)

        records.append({
            "question": question,
            "spans": spans,
            "stable": stable,
            "levels": per_level,
            "confidence": [round(r.confidence, 4) for r in readings],
        })

        mark = "stable " if stable else "FLIPPED"
        print(f"{mark} {question}")
        print(f"    spans: {sorted(set(spans))}")
        for level, info in per_level.items():
            print(f"    {level:<8} agree {info['agreement']:.2f}  picks={info['picks']}")
        print()

    print("=" * 68)
    print(f"identical span across all {args.runs} runs: {span_stable}/{len(questions)}")
    print("\nper-level self-agreement (share of runs matching the modal pick):")
    for level, shares in level_agreement.items():
        mean = sum(shares) / len(shares)
        print(f"  {level:<8} {mean:.3f}")

    # What pure chance would give, for comparison.
    corpus = resolver.corpus
    mean_chapters = corpus.chapter_total / len(corpus.books)
    print("\nchance baselines (uniform pick):")
    print(f"  book-level top probability by chance: {1/66:.4f}")
    print(f"  full-chain confidence by chance: ~{1/corpus.verse_total:.7f} (1 of "
          f"{corpus.verse_total} verses)")

    out = pathlib.Path(__file__).resolve().parent.parent / "experiments" / "stability.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"runs": args.runs, "records": records}, indent=2))
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

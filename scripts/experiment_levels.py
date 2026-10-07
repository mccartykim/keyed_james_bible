#!/usr/bin/env python3
"""Probe how well Jev discriminates at each level of the Bible hierarchy.

This is the decisive question for the project: Jev returns calibrated
probabilities over a fixed option set, so the question is whether it can pick
a *verse* out of 176 candidates and still produce something meaningful and
stable. If verse-level selection is noise, the whole design has to change.

Measures, per level: token cost, latency, confidence, and determinism
(the same question asked twice).

Run: python3 scripts/experiment_levels.py
"""

from __future__ import annotations

import json
import pathlib
import statistics
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from jev_kjv.corpus import Corpus  # noqa: E402
from jev_kjv.jev import JevClient  # noqa: E402

PROBES = [
    "Why do bad things happen to good people?",
    "Is it wrong to eat meat?",
    "How should I treat my neighbour?",
    "What happens after I die?",
    "Should I forgive someone who hurt me?",
]


def truncate(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1].rstrip() + "…"


def describe(label: str, result, expected: str | None = None) -> dict:
    answer = result.answers["pick"]
    ranked = answer.ranked()
    top = ranked[0][1] if ranked else 0.0
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    return {
        "level": label,
        "choice": answer.choice,
        "confidence": round(answer.confidence, 3),
        "top_prob": round(top, 3),
        "second_prob": round(second, 3),
        "options": len(answer.probabilities),
        "nonzero": sum(1 for p in answer.probabilities.values() if p > 0),
        "probs_sum": round(sum(answer.probabilities.values()), 4),
        "in_tokens": result.usage.input_tokens,
        "cost_usd": round(result.usage.cost, 6),
        "latency_ms": result.latency_ms,
        "matches_expected": (answer.choice == expected) if expected else None,
    }


def main() -> int:
    corpus = Corpus.load()
    client = JevClient()

    question = PROBES[0]
    state = {"question": question}
    print(f"question: {question!r}\n")

    rows = []

    # --- Level 1: book, 66 options described by name only -------------------
    book_criteria = {b.name: None for b in corpus.books}
    t = time.time()
    r = client.choice(state, "Which book of the Bible does this question hash to?", book_criteria, "pick")
    rows.append(describe("book (66)", r))
    book_choice = r.answers["pick"].choice
    print(f"book -> {book_choice}  ({time.time() - t:.1f}s wall)")

    # --- Level 2: chapter, every chapter of the chosen book -----------------
    book = corpus.book(book_choice)
    chapter_criteria = {str(c + 1): None for c in range(book.chapter_count)}
    r = client.choice(
        state,
        "Which chapter of this book does this question hash to?",
        chapter_criteria,
        "pick",
    )
    rows.append(describe(f"chapter ({book.chapter_count} opts)", r))
    chapter_choice = int(r.answers["pick"].choice)
    print(f"chapter -> {book.name} {chapter_choice}")

    # --- Level 3a: verse by number only (no text) --------------------------
    verses = corpus.chapter_verses(book.name, chapter_choice)
    number_criteria = {str(v.verse): None for v in verses}
    r = client.choice(
        state,
        "Which verse does this question hash to?",
        number_criteria,
        "pick",
    )
    rows.append(describe(f"verse-numbers ({len(verses)} opts)", r))
    print(f"verse (numbers only) -> {r.answers['pick'].choice}")

    # --- Level 3b: verse described by its full text ------------------------
    text_criteria = {str(v.verse): v.text for v in verses}
    r = client.choice(
        state,
        "Which verse does this question hash to?",
        text_criteria,
        "pick",
    )
    rows.append(describe(f"verse-text ({len(verses)} opts)", r))
    text_pick = int(r.answers["pick"].choice)
    print(f"verse (full text) -> {text_pick}: {verses[text_pick - 1].text}")

    # --- Level 3c: verse described by a clipped 60-char excerpt ------------
    short_criteria = {str(v.verse): truncate(v.text, 60) for v in verses}
    r = client.choice(
        state,
        "Which verse does this question hash to?",
        short_criteria,
        "pick",
    )
    rows.append(describe(f"verse-short ({len(verses)} opts)", r))
    print(f"verse (60-char) -> {r.answers['pick'].choice}")

    # --- The headline risk: is it deterministic across repeats? ------------
    print("\n--- determinism: same request 5x, full verse text ---")
    picks = []
    confs = []
    for i in range(5):
        r = client.choice(
            state,
            "Which verse does this question hash to?",
            text_criteria,
            "pick",
        )
        a = r.answers["pick"]
        picks.append(int(a.choice))
        confs.append(a.confidence)
    print(f"picks: {picks}  distinct={len(set(picks))}")
    print(f"confidence: {[round(c, 3) for c in confs]}")
    print(f"mean confidence: {statistics.mean(confs):.3f}")

    print("\n--- per-level table ---")
    print(f"{'level':<28}{'choice':>8}{'conf':>7}{'top':>7}{'2nd':>7}{'opts':>6}{'nz':>5}{'tok':>7}{'cost$':>10}{'ms':>7}")
    for row in rows:
        print(
            f"{row['level']:<28}{row['choice']:>8}{row['confidence']:>7.3f}"
            f"{row['top_prob']:>7.3f}{row['second_prob']:>7.3f}{row['options']:>6}"
            f"{row['nonzero']:>5}{row['in_tokens']:>7}{row['cost_usd']:>10.6f}"
            f"{row['latency_ms']:>7}"
        )

    out = pathlib.Path(__file__).resolve().parent.parent / "experiments" / "levels.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"probes": PROBES, "rows": rows}, indent=2))
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

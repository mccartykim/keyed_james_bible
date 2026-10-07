#!/usr/bin/env python3
"""Ask Jev what it makes of this project.

Feeds the actual source tree in as `state` and asks two independent Noul
questions about it in a single request. Noul returns the probability of yes,
so the answer is a calibrated number rather than prose.

Run: python3 scripts/ask_jev_about_itself.py
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from jev_kjv.jev import JevClient  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent

# The whole project, because the question is about what it *is*: judging it from
# a summary would answer a question about the summary instead.
FILES = [
    "README.md",
    "ask.py",
    "server.py",
    "jev_kjv/corpus.py",
    "jev_kjv/jev.py",
    "jev_kjv/resolver.py",
    "web/index.html",
    "web/app.js",
    "docs/how-it-keys.md",
    "flake.nix",
    "scripts/build_corpus.py",
]


def main() -> int:
    sources = {}
    for rel in FILES:
        path = ROOT / rel
        if path.is_file():
            sources[rel] = path.read_text()

    total = sum(len(v) for v in sources.values())
    print(f"{len(sources)} files, {total:,} characters (~{total // 4:,} tokens)\n")

    state = {
        "project": "keyed james bible",
        "language": "Python (standard library only) + plain HTML/CSS/JS",
        "what_it_does": (
            "Takes a question typed by a user and hashes it into a span of King "
            "James Version verses. It asks Jev (a structured decision model that "
            "returns probabilities over options rather than generated text) to "
            "choose a book, then a chapter, then a verse, then how many verses to "
            "read. The verse TEXT is never generated: it is clipped verbatim from "
            "a local public-domain KJV corpus. Results are cached so the same "
            "question returns the same span. Served on a public web page."
        ),
        "source_files": sources,
    }

    client = JevClient()
    result = client.ask(
        state,
        {
            "blasphemous": {
                "type": "noul",
                "instructions": (
                    "Considering the project as actually implemented, is it "
                    "blasphemous?"
                ),
                "criteria": {
                    "true": (
                        "The project mocks, demeans, or profanes God, Christ, the "
                        "Holy Spirit, scripture, or Christian worship; or it claims "
                        "divine authority or inerrancy for its own output; or it "
                        "presents itself as speaking for God."
                    ),
                    "false": (
                        "The project treats the Bible with respect as a text, does "
                        "not claim to speak for God, and does not mock or demean "
                        "Christian belief. It may be irreverent, playful, "
                        "technically odd, or theologically naive without being "
                        "blasphemous."
                    ),
                },
            },
            "useful": {
                "type": "noul",
                "instructions": (
                    "Considering the project as actually implemented, is it a "
                    "useful Bible study tool?"
                ),
                "criteria": {
                    "true": (
                        "Someone studying the Bible, or reflecting on a question, "
                        "would plausibly get something worthwhile from the passage "
                        "it returns: the verses are real, accurately quoted, "
                        "readable in context, and the way a passage is chosen is "
                        "transparent enough to reason about."
                    ),
                    "false": (
                        "The selection is effectively arbitrary or random, so the "
                        "passage returned carries no more meaning than a random "
                        "verse generator, and it would mislead anyone who trusted "
                        "it as a study aid."
                    ),
                },
            },
        },
    )

    print("=== answers ===")
    for name, label in [
        ("blasphemous", "Is it blasphemous?"),
        ("useful", "Is it a useful Bible study tool?"),
    ]:
        answer = result.noul(name)
        print(f"\n  {label}")
        print(f"    P(yes) = {answer.noul:.4f}   {'█' * round(answer.noul * 40):<40}")
        verdict = "yes" if answer.is_yes else "no"
        print(f"    leans {verdict} (lean {answer.lean:.3f})")

    print("\n=== usage ===")
    print(f"  model:        {result.model}")
    print(f"  input tokens: {result.usage.input_tokens:,}")
    print(f"  cost:         ${result.usage.cost:.6f}")
    print(f"  latency:      {result.latency_ms} ms")

    out = ROOT / "experiments" / "jev-self-assessment.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(result.raw, indent=2))
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

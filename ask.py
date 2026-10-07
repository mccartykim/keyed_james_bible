#!/usr/bin/env python3
"""Ask a question, get a span of KJV verses.

    python3 ask.py "Why do bad things happen to good people?"
    python3 ask.py --json "Is it wrong to eat meat?"
    echo "What happens after I die?" | python3 ask.py

The question is hashed into a key by chaining Jev Choice questions down the
Bible's structure (book, chapter, verse, extent), and the verses are then
clipped from the KJV corpus and printed with their confidence.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from jev_kjv.resolver import Resolver  # noqa: E402


def render(reading, colour: bool) -> str:
    """Human-readable rendering of a reading."""
    if colour:
        dim, strong, reset = "\033[2m", "\033[1m", "\033[0m"
    else:
        dim = strong = reset = ""

    lines = []
    lines.append("")
    lines.append(f'  {dim}Q{reset}  {reading.question}')
    lines.append("")

    conf = reading.confidence
    bar_width = 20
    filled = round(conf * bar_width)
    bar = "█" * filled + "░" * (bar_width - filled)
    lines.append(
        f"  {strong}{reading.reference}{reset}"
        f"   {dim}chain confidence {bar} {conf:.3f}{reset}"
    )
    lines.append("")

    width = max((len(v.reference) for v in reading.verses), default=0)
    for verse in reading.verses:
        lines.append(f"  {dim}{verse.reference.ljust(width)}{reset}  {verse.text}")
        lines.append("")

    lines.append(f"  {dim}how it was chosen{reset}")
    for trace in reading.traces:
        alts = ", ".join(
            f"{a['option']} {a['probability']:.2f}" for a in trace.alternatives[1:]
        )
        alts = f"   {dim}next: {alts}{reset}" if alts else ""
        lines.append(
            f"    {trace.level.ljust(8)} {str(trace.choice).ljust(6)} "
            f"{dim}{trace.confidence:.3f} of {trace.options} options{reset}{alts}"
        )

    lines.append("")
    tag = "cache hit" if reading.cached else "live"
    lines.append(
        f"  {dim}{tag} · ${reading.total_cost_usd:.6f} · "
        f"{reading.total_latency_ms} ms · {reading.model}{reset}"
    )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Turn a question into a span of KJV verses using Jev."
    )
    parser.add_argument("question", nargs="*", help="the question to ask")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    parser.add_argument("--no-cache", action="store_true", help="ignore the result cache")
    parser.add_argument("--no-colour", "--no-color", dest="no_colour", action="store_true")
    args = parser.parse_args(argv)

    question = " ".join(args.question).strip()
    if not question:
        if sys.stdin.isatty():
            parser.error("no question given")
        question = sys.stdin.read().strip()
    if not question:
        parser.error("no question given")

    resolver = Resolver(use_cache=not args.no_cache)
    try:
        reading = resolver.resolve(question)
    except Exception as exc:  # surface the reason rather than a bare traceback
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(reading.as_dict(), indent=2, ensure_ascii=False))
    else:
        colour = sys.stdout.isatty() and not args.no_colour
        print(render(reading, colour))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Normalize the per-book KJV JSON files into a single canonical corpus.

Source: https://github.com/aruljohn/Bible-kjv (public domain KJV).
Each source file has the shape:
    {"book": "Genesis",
     "chapters": [{"chapter": "1", "verses": [{"verse": "1", "text": "..."}]}]}

Output: data/kjv.json
    {"books": [{"name": "Genesis", "slug": "Genesis", "testament": "OT",
                "index": 0, "chapters": [["verse 1 text", "verse 2 text", ...]]}]}

Verse text is stored as bare strings; array position encodes the verse number.
Chapter arrays are 0-indexed the same way: chapters[c][v] is chapter c+1,
verse v+1.
"""

from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "kjv"
DEST = ROOT / "data" / "kjv.json"

# Canonical KJV order: 39 Old Testament books, then 27 New Testament books.
CANON: list[tuple[str, str]] = [
    ("Genesis", "OT"), ("Exodus", "OT"), ("Leviticus", "OT"), ("Numbers", "OT"),
    ("Deuteronomy", "OT"), ("Joshua", "OT"), ("Judges", "OT"), ("Ruth", "OT"),
    ("1 Samuel", "OT"), ("2 Samuel", "OT"), ("1 Kings", "OT"), ("2 Kings", "OT"),
    ("1 Chronicles", "OT"), ("2 Chronicles", "OT"), ("Ezra", "OT"),
    ("Nehemiah", "OT"), ("Esther", "OT"), ("Job", "OT"), ("Psalms", "OT"),
    ("Proverbs", "OT"), ("Ecclesiastes", "OT"), ("Song of Solomon", "OT"),
    ("Isaiah", "OT"), ("Jeremiah", "OT"), ("Lamentations", "OT"), ("Ezekiel", "OT"),
    ("Daniel", "OT"), ("Hosea", "OT"), ("Joel", "OT"), ("Amos", "OT"),
    ("Obadiah", "OT"), ("Jonah", "OT"), ("Micah", "OT"), ("Nahum", "OT"),
    ("Habakkuk", "OT"), ("Zephaniah", "OT"), ("Haggai", "OT"), ("Zechariah", "OT"),
    ("Malachi", "OT"),
    ("Matthew", "NT"), ("Mark", "NT"), ("Luke", "NT"), ("John", "NT"), ("Acts", "NT"),
    ("Romans", "NT"), ("1 Corinthians", "NT"), ("2 Corinthians", "NT"),
    ("Galatians", "NT"), ("Ephesians", "NT"), ("Philippians", "NT"),
    ("Colossians", "NT"), ("1 Thessalonians", "NT"), ("2 Thessalonians", "NT"),
    ("1 Timothy", "NT"), ("2 Timothy", "NT"), ("Titus", "NT"), ("Philemon", "NT"),
    ("Hebrews", "NT"), ("James", "NT"), ("1 Peter", "NT"), ("2 Peter", "NT"),
    ("1 John", "NT"), ("2 John", "NT"), ("3 John", "NT"), ("Jude", "NT"),
    ("Revelation", "NT"),
]

# Independent check of KJV structure. If the downloaded text disagrees with this
# table, the data is wrong rather than the table, and the build should fail loudly.
EXPECTED_CHAPTERS: dict[str, int] = {
    "Genesis": 50, "Exodus": 40, "Leviticus": 27, "Numbers": 36, "Deuteronomy": 34,
    "Joshua": 24, "Judges": 21, "Ruth": 4, "1 Samuel": 31, "2 Samuel": 24,
    "1 Kings": 22, "2 Kings": 25, "1 Chronicles": 29, "2 Chronicles": 36,
    "Ezra": 10, "Nehemiah": 13, "Esther": 10, "Job": 42, "Psalms": 150,
    "Proverbs": 31, "Ecclesiastes": 12, "Song of Solomon": 8, "Isaiah": 66,
    "Jeremiah": 52, "Lamentations": 5, "Ezekiel": 48, "Daniel": 12, "Hosea": 14,
    "Joel": 3, "Amos": 9, "Obadiah": 1, "Jonah": 4, "Micah": 7, "Nahum": 3,
    "Habakkuk": 3, "Zephaniah": 3, "Haggai": 2, "Zechariah": 14, "Malachi": 4,
    "Matthew": 28, "Mark": 16, "Luke": 24, "John": 21, "Acts": 28, "Romans": 16,
    "1 Corinthians": 16, "2 Corinthians": 13, "Galatians": 6, "Ephesians": 6,
    "Philippians": 4, "Colossians": 4, "1 Thessalonians": 5, "2 Thessalonians": 3,
    "1 Timothy": 6, "2 Timothy": 4, "Titus": 3, "Philemon": 1, "Hebrews": 13,
    "James": 5, "1 Peter": 5, "2 Peter": 3, "1 John": 5, "2 John": 1, "3 John": 1,
    "Jude": 1, "Revelation": 22,
}

EXPECTED_TOTAL_VERSES = 31102
EXPECTED_TOTAL_CHAPTERS = 1189


def slug_for(name: str) -> str:
    """Map a display name to the source repository's filename stem."""
    return name.replace(" ", "")


def main() -> int:
    missing = [n for n, _ in CANON if not (SRC / f"{slug_for(n)}.json").exists()]
    if missing:
        print(f"missing source files for: {missing}", file=sys.stderr)
        return 1

    books = []
    total_chapters = 0
    total_verses = 0
    errors: list[str] = []

    for index, (name, testament) in enumerate(CANON):
        raw = json.loads((SRC / f"{slug_for(name)}.json").read_text())
        if raw["book"] != name:
            errors.append(f"{name}: source file reports book name {raw['book']!r}")

        chapters: list[list[str]] = []
        for ci, chapter in enumerate(raw["chapters"], start=1):
            verses = chapter["verses"]
            texts = []
            for vi, verse in enumerate(verses, start=1):
                if int(verse["verse"]) != vi:
                    errors.append(f"{name} {ci}:{vi}: source verse number is {verse['verse']}")
                texts.append(verse["text"].strip())
            if not texts:
                errors.append(f"{name} {ci}: chapter has no verses")
            chapters.append(texts)

        expected = EXPECTED_CHAPTERS[name]
        if len(chapters) != expected:
            errors.append(f"{name}: {len(chapters)} chapters, expected {expected}")

        total_chapters += len(chapters)
        total_verses += sum(len(c) for c in chapters)
        books.append({
            "name": name,
            "slug": slug_for(name),
            "testament": testament,
            "index": index,
            "chapters": chapters,
        })

    if total_chapters != EXPECTED_TOTAL_CHAPTERS:
        errors.append(f"total chapters {total_chapters} != {EXPECTED_TOTAL_CHAPTERS}")
    if total_verses != EXPECTED_TOTAL_VERSES:
        errors.append(f"total verses {total_verses} != {EXPECTED_TOTAL_VERSES}")

    if errors:
        print("corpus verification FAILED:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    DEST.write_text(json.dumps({"books": books}, ensure_ascii=False, separators=(",", ":")))
    size_mb = DEST.stat().st_size / 1e6
    print(f"wrote {DEST} ({size_mb:.1f} MB)")
    print(f"verified {len(books)} books, {total_chapters} chapters, {total_verses} verses")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

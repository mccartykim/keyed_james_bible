"""Load and address the KJV corpus.

The corpus is built by scripts/build_corpus.py from the per-book JSON files in
data/kjv/, which come from https://github.com/aruljohn/Bible-kjv (public domain).

Addressing is by book name, 1-based chapter, 1-based verse. Chapter and verse
numbers are never stored in the data; array position encodes them, so the
in-memory representation stays compact.
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass
from functools import cached_property

ROOT = pathlib.Path(__file__).resolve().parent.parent
CORPUS_PATH = ROOT / "data" / "kjv.json"


@dataclass(frozen=True)
class Verse:
    book: str
    chapter: int
    verse: int
    text: str

    @property
    def reference(self) -> str:
        return f"{self.book} {self.chapter}:{self.verse}"

    def as_dict(self) -> dict[str, object]:
        return {
            "book": self.book,
            "chapter": self.chapter,
            "verse": self.verse,
            "reference": self.reference,
            "text": self.text,
        }


@dataclass(frozen=True)
class Book:
    name: str
    slug: str
    testament: str
    index: int
    chapters: list[list[str]]

    @property
    def chapter_count(self) -> int:
        return len(self.chapters)

    def verse_count(self, chapter: int) -> int:
        return len(self.chapters[chapter - 1])


class Corpus:
    """The whole KJV, addressable by book, chapter, and verse."""

    def __init__(self, books: list[Book]) -> None:
        self.books = books
        self._by_name = {b.name.lower(): b for b in books}
        self._by_slug = {b.slug.lower(): b for b in books}

    @classmethod
    def load(cls, path: pathlib.Path | str | None = None) -> "Corpus":
        path = pathlib.Path(path) if path else CORPUS_PATH
        if not path.is_file():
            raise FileNotFoundError(
                f"{path} not found. Run `python3 scripts/build_corpus.py` first."
            )
        raw = json.loads(path.read_text())
        books = [
            Book(
                name=b["name"],
                slug=b["slug"],
                testament=b["testament"],
                index=b["index"],
                chapters=b["chapters"],
            )
            for b in raw["books"]
        ]
        return cls(books)

    @cached_property
    def book_names(self) -> list[str]:
        return [b.name for b in self.books]

    @cached_property
    def verse_total(self) -> int:
        return sum(len(c) for b in self.books for c in b.chapters)

    @cached_property
    def chapter_total(self) -> int:
        return sum(b.chapter_count for b in self.books)

    def book(self, name: str) -> Book:
        """Look up a book, tolerating case and the source repo's slug spelling."""
        key = name.strip().lower()
        found = self._by_name.get(key) or self._by_slug.get(key.replace(" ", ""))
        if found is None:
            # Accept the filename-style spelling ("SongofSolomon", "1Samuel").
            found = self._by_slug.get(key)
        if found is None:
            raise KeyError(f"unknown book: {name!r}")
        return found

    def verse(self, book: str, chapter: int, verse: int) -> Verse:
        b = self.book(book)
        if not 1 <= chapter <= b.chapter_count:
            raise KeyError(f"{b.name} has {b.chapter_count} chapters, not {chapter}")
        texts = b.chapters[chapter - 1]
        if not 1 <= verse <= len(texts):
            raise KeyError(
                f"{b.name} {chapter} has {len(texts)} verses, not {verse}"
            )
        return Verse(b.name, chapter, verse, texts[verse - 1])

    def chapter_verses(self, book: str, chapter: int) -> list[Verse]:
        b = self.book(book)
        return [
            Verse(b.name, chapter, i, text)
            for i, text in enumerate(b.chapters[chapter - 1], start=1)
        ]

    def span(self, book: str, chapter: int, start: int, end: int) -> list[Verse]:
        """Verses start..end inclusive, all within one chapter."""
        if end < start:
            start, end = end, start
        return self.chapter_verses(book, chapter)[start - 1 : end]

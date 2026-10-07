"""Turn a question into a span of KJV verses by chaining Jev Choice questions.

The chain is book -> chapter -> anchor verse -> extent. Each level is a separate
request, because Jev answers every question in a batch independently and in
parallel: a question cannot see another question's answer within one call. The
level-by-level chaining is the hierarchical-classification pattern from the
TypeSafe cookbook.

Observed behaviour of Jev on this task (see experiments/levels.json):
  - Books discriminate very strongly when described by genre and theme.
  - Chapters and verses discriminate weakly but above chance; the answer is
    still usable as a deterministic key, but the confidence value is the
    honest measure of how much signal was there. Low-confidence levels are
    surfaced rather than hidden.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
from dataclasses import asdict, dataclass, field
from typing import Any

from .corpus import Corpus, Verse
from .jev import JevClient, JevError

ROOT = pathlib.Path(__file__).resolve().parent.parent
CACHE_PATH = ROOT / "data" / "cache.json"


def default_cache_path() -> pathlib.Path:
    """Where resolved mappings are cached.

    KJB_CACHE_PATH overrides it. That matters when the app is packaged: the
    source tree then lives read-only in the Nix store, so the default location
    next to the source cannot be written and the service must be pointed at a
    state directory instead.
    """
    override = os.environ.get("KJB_CACHE_PATH")
    if override:
        return pathlib.Path(override)
    return CACHE_PATH

# One-line genre/theme descriptors. These are the option descriptions for the
# book-level Choice; Jev sees the description and not the option name alone.
BOOK_DESCRIPTORS: dict[str, str] = {
    "Genesis": "Beginnings: creation, the patriarchs, the covenant with Abraham",
    "Exodus": "Deliverance from Egypt, the law, the tabernacle",
    "Leviticus": "Laws of sacrifice, holiness, priestly worship",
    "Numbers": "Wilderness wanderings and the census of Israel",
    "Deuteronomy": "Moses' final speeches renewing the covenant",
    "Joshua": "Entry into and conquest of the promised land",
    "Judges": "Cycles of apostasy, oppression, and deliverance",
    "Ruth": "Loyalty and redemption in a Moabite widow's family",
    "1 Samuel": "Samuel, Saul, and the rise of David",
    "2 Samuel": "David's reign, his triumphs and his fall",
    "1 Kings": "Solomon, the divided kingdom, Elijah",
    "2 Kings": "The fall of Israel and Judah, Elisha",
    "1 Chronicles": "Genealogies and David's reign retold for the temple",
    "2 Chronicles": "Solomon's temple and the kings of Judah",
    "Ezra": "Return from exile and rebuilding the temple",
    "Nehemiah": "Rebuilding the walls of Jerusalem",
    "Esther": "A queen risks her life to save her people",
    "Job": "Suffering, lament, and the fear of God",
    "Psalms": "Songs and prayers: praise, lament, thanksgiving, trust",
    "Proverbs": "Wisdom for daily living",
    "Ecclesiastes": "The limits of earthly life and labour",
    "Song of Solomon": "Poetry of love and desire",
    "Isaiah": "Judgment and comfort, the coming servant",
    "Jeremiah": "Warnings of exile and the new covenant",
    "Lamentations": "Grief over the destruction of Jerusalem",
    "Ezekiel": "Visions of judgment and restoration",
    "Daniel": "Faith under empire and visions of the end",
    "Hosea": "God's unfailing love for an unfaithful people",
    "Joel": "Locusts, repentance, and the day of the Lord",
    "Amos": "Justice and judgment on a complacent nation",
    "Obadiah": "Judgment on Edom",
    "Jonah": "A reluctant prophet and a merciful God",
    "Micah": "Justice, mercy, and walking humbly",
    "Nahum": "The fall of Nineveh",
    "Habakkuk": "A prophet questions God's justice",
    "Zephaniah": "The day of the Lord and a surviving remnant",
    "Haggai": "Rebuild the temple",
    "Zechariah": "Visions of restoration and a coming king",
    "Malachi": "Covenant faithfulness and a coming messenger",
    "Matthew": "Jesus the Messiah: teaching, parables, the kingdom",
    "Mark": "Jesus' ministry and passion, told at pace",
    "Luke": "Jesus' compassion, parables, and the outsider",
    "John": "Jesus' identity, signs, and eternal life",
    "Acts": "The Spirit, the early church, Paul's mission",
    "Romans": "Justification by faith and life in the Spirit",
    "1 Corinthians": "Church order, spiritual gifts, love",
    "2 Corinthians": "Paul's defence of his ministry",
    "Galatians": "Freedom from the law",
    "Ephesians": "The church as the body of Christ",
    "Philippians": "Joy in hardship",
    "Colossians": "The supremacy of Christ",
    "1 Thessalonians": "Christ's return and holy living",
    "2 Thessalonians": "Steadfastness awaiting the day of the Lord",
    "1 Timothy": "Church leadership and sound doctrine",
    "2 Timothy": "Guarding the faith; Paul's last letter",
    "Titus": "Ordering churches and good works",
    "Philemon": "Forgiveness for a runaway slave",
    "Hebrews": "Christ as the better high priest and sacrifice",
    "James": "Faith proved by works and by speech",
    "1 Peter": "Suffering, hope, and holy living",
    "2 Peter": "False teachers and the day of the Lord",
    "1 John": "Assurance, love, and truth",
    "2 John": "Walking in truth and love",
    "3 John": "Hospitality and conflict in the church",
    "Jude": "Contending for the faith against corruption",
    "Revelation": "Visions of judgment, the Lamb, the new creation",
}

# Extent is how many verses the reading covers, starting at the anchor. The
# code applies it as an offset (verses - 1), but the option keys sent to Jev are
# verse counts rather than offsets, and that is deliberate: an option keyed "0"
# acts as a sticky default. Tested over six questions, a 0-based option set was
# chosen as "0" five times out of six regardless of how the options or the
# instruction were phrased, collapsing the choice. The same set relabelled from
# 1 varies normally.
#
# Its instruction is asked in the same request as the book question, because
# extent depends on nothing else in the chain: both only need the bare question.
# Jev answers questions in a request independently, so batching them is exactly
# the intended use, and it costs one fewer round trip.
EXTENT_CRITERIA: dict[str, str] = {
    "1": "A single verse is enough",
    "2": "Two verses: the anchor and the one after it",
    "3": "Three verses: a short complete thought",
    "5": "Four to six verses: a longer passage",
    "8": "Nine verses or so: a long passage",
}
EXTENT_VERSES: dict[str, int] = {k: int(k) for k in EXTENT_CRITERIA}
DEFAULT_EXTENT_VERSES = 1

# Verse options are described by an excerpt rather than the full text. Excerpts
# measured both cheaper and slightly better-discriminating than full verse text.
VERSE_EXCERPT_CHARS = 72


# Bump when the question wording or the state assembled at any level changes.
# It is part of the cache key, so an existing mapping is never re-used under a
# prompt it was not produced by.
RECIPE_VERSION = 3

# Display order for the chain, which is not the order the requests are made in:
# book and extent share the first request, so extent is shown last.
LEVEL_ORDER = ["book", "chapter", "verse", "extent"]


def normalize_question(question: str) -> str:
    return " ".join(question.split()).strip()


def excerpt(text: str, width: int = VERSE_EXCERPT_CHARS) -> str:
    if len(text) <= width:
        return text
    return text[: width - 1].rstrip() + "…"


@dataclass
class LevelTrace:
    """What Jev decided at one level of the chain."""

    level: str
    options: int
    choice: str
    confidence: float
    probabilities: dict[str, float] = field(default_factory=dict)
    input_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    request: int = 0
    alternatives: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Reading:
    """A resolved question: the verses, and how they were chosen."""

    question: str
    verses: list[Verse]
    traces: list[LevelTrace]
    cached: bool = False
    model: str = ""

    @property
    def reference(self) -> str:
        if not self.verses:
            return ""
        first, last = self.verses[0], self.verses[-1]
        if first.verse == last.verse:
            return first.reference
        return f"{first.book} {first.chapter}:{first.verse}-{last.verse}"

    @property
    def confidence(self) -> float:
        """Product of the per-level confidences: the chain's joint confidence."""
        value = 1.0
        for t in self.traces:
            value *= t.confidence
        return value

    @property
    def total_cost_usd(self) -> float:
        """What the mapping cost when it was first resolved.

        Levels that share a request report that request's usage identically, so
        cost is counted once per request rather than once per level. A cache hit
        re-uses a mapping that was already paid for, so this is the original
        price rather than a new charge.
        """
        return sum(self._per_request_cost().values())

    @property
    def total_latency_ms(self) -> int:
        """Time spent calling Jev, counted once per request.

        Book and extent are answered in a single request, so summing per-level
        latencies would bill that round trip twice. Zero when cached.
        """
        if self.cached:
            return 0
        return sum(self._per_request_latency().values())

    def _per_request_cost(self) -> dict[int, float]:
        seen: dict[int, float] = {}
        for trace in self.traces:
            seen.setdefault(trace.request, trace.cost_usd)
        return seen

    def _per_request_latency(self) -> dict[int, int]:
        seen: dict[int, int] = {}
        for trace in self.traces:
            seen.setdefault(trace.request, trace.latency_ms)
        return seen

    @property
    def request_count(self) -> int:
        return len({t.request for t in self.traces})

    def text(self) -> str:
        return " ".join(v.text for v in self.verses)

    def as_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "reference": self.reference,
            "verses": [v.as_dict() for v in self.verses],
            "confidence": round(self.confidence, 4),
            "level_confidence": {t.level: round(t.confidence, 4) for t in self.traces},
            "selection": [asdict(t) for t in self.traces],
            "cached": self.cached,
            "model": self.model,
            "cost_usd": round(self.total_cost_usd, 6),
            "latency_ms": self.total_latency_ms,
        }


class Resolver:
    """Resolves questions into KJV verse spans, with an on-disk cache."""

    def __init__(
        self,
        corpus: Corpus | None = None,
        client: JevClient | None = None,
        cache_path: pathlib.Path | str | None = None,
        use_cache: bool = True,
        alternatives: int = 3,
    ) -> None:
        self.corpus = corpus or Corpus.load()
        self.client = client or JevClient()
        self.cache_path = (
            pathlib.Path(cache_path) if cache_path else default_cache_path()
        )
        self.use_cache = use_cache
        self.alternatives = alternatives

    # ---------------------------------------------------------------- cache

    def _cache_key(self, question: str) -> str:
        payload = (
            f"v{RECIPE_VERSION}\0{self.client.model}\0"
            f"{normalize_question(question).lower()}"
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:32]

    def _read_cache(self) -> dict[str, Any]:
        if not self.cache_path.is_file():
            return {}
        try:
            return json.loads(self.cache_path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}

    def _write_cache(self, key: str, reading: Reading) -> None:
        """Persist a resolved mapping. Best effort only.

        A failed write must not fail the request: the mapping is already
        computed and the answer is correct, so an unwritable or full state
        directory costs a cache entry, not a served response. This is why the
        failure is swallowed rather than raised.
        """
        if not self.use_cache:
            return
        data = self._read_cache()
        data[key] = {
            "question": reading.question,
            "traces": [asdict(t) for t in reading.traces],
            "model": reading.model,
        }
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.cache_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
            os.replace(tmp, self.cache_path)
        except OSError:
            return

    # -------------------------------------------------------------- resolve

    def resolve(self, question: str, use_cache: bool | None = None) -> Reading:
        """Map a question to a span of verses.

        The same question returns the same span: results are cached by question
        text and model, and the verse text itself is always re-read from the
        corpus so cached references cannot go stale against the source.
        """
        question = normalize_question(question)
        if not question:
            raise ValueError("question must not be empty")

        use_cache = self.use_cache if use_cache is None else use_cache
        key = self._cache_key(question)
        if use_cache:
            hit = self._read_cache().get(key)
            if hit:
                return self._from_cache(question, hit)

        traces: list[LevelTrace] = []
        model = self.client.model

        # Levels 1 and 4 together: which book, and how far past the anchor verse
        # should the reading run? Neither depends on the other, and extent does
        # not depend on the book or chapter at all, so they share one request.
        # Jev answers them in one pass, independently.
        state = {"question": question}
        criteria = {name: BOOK_DESCRIPTORS.get(name) for name in self.corpus.book_names}
        result = self.client.ask(
            state,
            {
                "book": {
                    "type": "choice",
                    "instructions": (
                        "A person asks this question. Which book of the Bible "
                        "does it map onto?"
                    ),
                    "criteria": criteria,
                },
                "extent": {
                    "type": "choice",
                    "instructions": (
                        "For this question, how far should the reading run from "
                        "the verse it lands on? Answer in verses."
                    ),
                    "criteria": EXTENT_CRITERIA,
                },
            },
        )
        model = result.model or model
        traces.append(self._trace("book", result, "book", self.corpus.book_names, 1))
        traces.append(self._trace("extent", result, "extent", list(EXTENT_CRITERIA), 1))

        book = self.corpus.book(result.choice("book").choice)
        verse_count = EXTENT_VERSES.get(result.choice("extent").choice, DEFAULT_EXTENT_VERSES)

        # Level 2: which chapter of that book? The book is fed back through the
        # state, which Jev shares across a request, rather than through another
        # question's answer, which one question cannot see.
        state = {"question": question, "book": book.name}
        chapter_criteria = {
            str(c): excerpt(" ".join(book.chapters[c - 1][:2]), 60)
            for c in range(1, book.chapter_count + 1)
        }
        result = self.client.choice(
            state,
            f"A person asks this question. Which chapter of {book.name} does it map onto?",
            chapter_criteria,
            "chapter",
        )
        chapter = int(result.choice("chapter").choice)
        traces.append(
            self._trace(
                "chapter", result, "chapter", [str(c) for c in range(1, book.chapter_count + 1)], 2
            )
        )

        # Level 3: which verse of that chapter? The chapter joins the state, so
        # this question is asked with the address built so far in view.
        state = {"question": question, "book": book.name, "chapter": chapter}
        verses = self.corpus.chapter_verses(book.name, chapter)
        verse_criteria = {str(v.verse): excerpt(v.text) for v in verses}
        result = self.client.choice(
            state,
            f"A person asks this question. Which verse of {book.name} {chapter} does it map onto?",
            verse_criteria,
            "verse",
        )
        anchor = int(result.choice("verse").choice)
        traces.append(
            self._trace("verse", result, "verse", [str(v.verse) for v in verses], 3)
        )

        # The extent chosen in the first request counts verses from the anchor,
        # so the reading is the anchor plus that many verses, clipped to the end
        # of the chapter.
        start = anchor
        end = min(anchor + verse_count - 1, len(verses))
        traces.sort(key=lambda t: LEVEL_ORDER.index(t.level))

        reading = Reading(
            question=question,
            verses=self.corpus.span(book.name, chapter, start, end),
            traces=traces,
            model=model,
        )
        self._write_cache(key, reading)
        return reading

    def _trace(
        self, level: str, result, question_id: str, options: list[str], request: int
    ) -> LevelTrace:
        """Record one level's decision, including what it cost and how long it took.

        `request` numbers the HTTP request the level was answered in, so levels
        that share a round trip can be counted once when totalling usage.
        """
        answer = result.choice(question_id)
        ranked = answer.ranked()
        return LevelTrace(
            level=level,
            options=len(options),
            choice=answer.choice,
            confidence=answer.confidence,
            probabilities={k: round(v, 6) for k, v in answer.probabilities.items()},
            input_tokens=result.usage.input_tokens,
            cost_usd=result.usage.cost,
            latency_ms=result.latency_ms,
            request=request,
            alternatives=[
                {"option": k, "probability": round(v, 4)} for k, v in ranked[: self.alternatives]
            ],
        )

    def _from_cache(self, question: str, hit: dict[str, Any]) -> Reading:
        traces = [LevelTrace(**t) for t in hit["traces"]]
        by_level = {t.level: t for t in traces}
        book = self.corpus.book(by_level["book"].choice)
        chapter = int(by_level["chapter"].choice)
        anchor = int(by_level["verse"].choice)
        verse_count = EXTENT_VERSES.get(by_level["extent"].choice, DEFAULT_EXTENT_VERSES)
        verse_total = book.verse_count(chapter)
        end = min(anchor + verse_count - 1, verse_total)
        return Reading(
            question=question,
            verses=self.corpus.span(book.name, chapter, anchor, end),
            traces=traces,
            cached=True,
            model=hit.get("model", ""),
        )

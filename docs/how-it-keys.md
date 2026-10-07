# How a question gets keyed into the Bible

The question this document answers: *how does Jev find the right verse if book
and chapter are independent?*

Short answer: **Jev never finds "the right verse."** At each step it answers one
narrow question about a set of options that are all equally valid candidates.
The narrowing comes from the shape of the tree, not from Jev knowing anything
about the Bible as a whole.

---

## The chart

```
                         question
                  "Is it wrong to eat meat?"
                             │
                             ▼
      ┌──────────────────────────────────────────────┐
      │ REQUEST 1                          ~0.22 s   │
      │                                              │
      │ state    { question }                        │
      │                                              │
      │ TWO questions in one request, because neither│
      │ depends on the other:                        │
      │                                              │
      │ Q1 "Which book of the Bible does it map      │
      │     onto?"                                   │
      │     options 66 — every book, described by    │
      │     genre and theme                          │
      │                                              │
      │ Q2 "How far should the reading run from the  │
      │     verse it lands on?"                      │
      │     options 1 · 2 · 3 · 5 · 8 verses          │
      │     (a count, applied from the anchor)       │
      └──────────────────────────────────────────────┘
                             │
                    Jev returns TWO distributions
                     from the same single pass
                             ▼
        Romans 0.33 · 1 Corinthians 0.26 · Genesis 0.15 · …    (book)
        extent 1 [0.44] · 3 [0.26] · 2 [0.18] · …              (extent)
                             │
                  code takes the argmax of each
                             ▼
                    Romans  +  extent 1
                             │
                             ▼
      ┌──────────────────────────────────────────────┐
      │ REQUEST 2                                    │
      │                                              │
      │ state    { question, book: "Romans" }  ◄─────┼── the book
      │ question "Which chapter of Romans does it    │   travels in
      │           map onto?"                         │   the STATE
      │ options  16 — chapters 1..16, described by   │
      │          their opening words                 │
      └──────────────────────────────────────────────┘
                             │
                             ▼
                        ch 14  0.99
                             │
                             ▼
      ┌──────────────────────────────────────────────┐
      │ REQUEST 3                                    │
      │                                              │
      │ state    { question, book, chapter: 14 }     │
      │ question "Which verse of Romans 14 does it   │
      │           map onto?"                         │
      │ options  23 — verses 1..23, described by     │
      │          a text excerpt                      │
      └──────────────────────────────────────────────┘
                             │
                             ▼
                        v 2  0.34
                             │
                             ▼
                  code clips the span
                  anchor 2, extent 1 → verse 2 alone
      ═══════════════════════════════════════════════
                   Romans 14:2
      "For one believeth that he may eat all things:
       another, who is weak, eateth herbs."
      ═══════════════════════════════════════════════
```

---

## Why it can't be one call

This is the part that surprises people, and it's documented behaviour rather
than a guess. From TypeSafe's build guide:

> Questions are evaluated independently and in parallel. One primitive's result
> does not become hidden context that changes another primitive's result.

So the tempting version does not work:

```
  ┌────────────────── one Jev request ──────────────────┐
  │  state: { question }                                │
  │                                                     │
  │  Q1 "which book?"    ──►  Romans                    │
  │  Q2 "which chapter?" ──►  14      ┐                 │
  │  Q3 "which verse?"   ──►  ?       │ all three       │
  │                                   │ answered in     │
  │  Q2 is asked "which chapter?"     │ parallel, blind │
  │  with no book in scope — chapter  │ to each other   │
  │  of WHAT? It would have to answer │                 │
  │  for the whole Bible.             │                 │
  │                                   │                 │
  │  Q3 has no chapter to attach to.  ┘                 │
  └─────────────────────────────────────────────────────┘
```

And the version that works:

```
  request 1 ── state {question}                   ──►  book  +  extent
  request 2 ── state {question, book}             ──►  chapter
  request 3 ── state {question, book, chapter}    ──►  verse

  ──► answers never see each other
  ──► state is shared across everything in a request
  ──► so the chain runs through state, driven by code
```

**State is the channel. Answers are a dead end.** That's the whole trick, and
it's why the resolver makes three HTTP calls rather than one.

Note that it is three and not four: the book question and the extent question
are answered *together*, because extent does not depend on the book, the
chapter, or the verse. That is the one place the chain has slack, and batching
it saves a round trip (~0.9 s → ~0.7 s).

---

## What Jev actually sees

Jev is not reading scripture. It sees a JSON object and a list of options with
descriptions, and it ranks the options against each other. Nothing more.

```
      Level        what Jev is shown                     does it know the Bible?
  ──────────────────────────────────────────────────────────────────────────────
   book         66 names + one-line genre descriptors     no — just 66 blobs of
                                                          English prose
   chapter      "1", "2", … "16" + opening words          no — just 16 blobs
   verse        "1", "2", … "23" + text excerpt           no — just 23 blobs
   extent       4 phrasings of length                     no — pure preference
```

At every level all options are *legitimate*. There is no "correct" chapter of
Romans for a question about meat. Jev is not being asked to retrieve a fact;
it's being asked to break a tie among candidates that are all defensible.

---

## So it behaves like a hash, not like search

This is the honest characterisation, and it matters:

| | retrieval / search | this |
|---|---|---|
| goal | find the *relevant* passage | produce *a* passage, repeatably |
| same query twice | same answer (it's a lookup) | same answer (it's cached) |
| different query, same book | different chapter, guided by relevance | different chapter, guided by probability |
| failure mode | "wrong" answer | no wrong answer — only a less confident one |

Because chapter and verse are chosen independently given the prefix, a question
that lands on Romans can land on chapter 14 while a neighbouring question lands
on chapter 9 — and neither is an error. That's what makes it feel like a hash.
It is not a cryptographic one: the output is stable only because it's cached,
and the underlying model is a probability distribution that can move between
model versions.

---

## Where the confidence number comes from

```
  chain confidence = book_conf × chapter_conf × verse_conf × extent_conf

  Romans 14:2   =   0.33   ×    0.99      ×    0.34     ×     0.44     = 0.049
```

It is the product of four uncertain steps, so it is *small even when the model
is doing well*. Treat it as a relative signal — comparing two questions, or two
candidate spans — rather than as an absolute pass/fail mark. A low score at one
level usually means that level genuinely had a flat distribution, which is
information, not a bug.

## Two Jev behaviours worth knowing about

Both were found by testing, and both cost real time to diagnose, so they are
recorded here.

**Option keys are not neutral.** An option keyed `"0"` acts as a sticky default.
Given an extent question with options `0 · 1 · 2 · 3 · 5 · 8`, Jev chose `"0"`
on five of six questions, regardless of how the options or the instruction were
phrased — relabelling the same set from `1` restored normal variation. The
resolver therefore sends verse *counts* (`1 · 2 · 3 · 5 · 8`) and converts to an
offset in code.

**Option counts are bounded at 255.** Nothing here comes close: the largest
option set in the whole chain is 176, the verse level of Psalm 119. Chapters
max out at 150 (Psalms) and books at 66. The ceiling is not a constraint on this
design, but it is the ceiling that would rule out, say, a flat 31,102-verse
Choice.

---

## Knobs, if you want to change the behaviour

| want | change |
|---|---|
| longer / shorter passages | `EXTENT_CRITERIA` in `jev_kjv/resolver.py` |
| better book discrimination | `BOOK_DESCRIPTORS` — richer, more distinguishing |
| better verse discrimination | `VERSE_EXCERPT_CHARS` (72 measured well) |
| a different KJV edition | `data/kjv/` and re-run `scripts/build_corpus.py` |
| forced span length | `?span=5` on the API |
| ignore the cache | `?fresh=1` or `python3 ask.py --no-cache` |
| prompt changes | bump `RECIPE_VERSION`, so old cached mappings aren't re-used |

A quality upgrade worth trying later: instead of taking the argmax at each
level, keep the top few candidates at every step and score the resulting paths
(a beam search). TypeSafe document this as their hierarchical-classification
pattern. It would cost more calls but would let a strong verse override a weak
chapter choice.

---

## Run it

```bash
cd /mnt/bulk/shared_projects/keyed_james_bible

# one-off: build the corpus from the per-book source files (already done)
python3 scripts/build_corpus.py

# ask from the command line
python3 ask.py "Is it wrong to eat meat?"
python3 ask.py --json "What happens after I die?"
python3 ask.py --no-cache "What should I do when I am afraid?"

# a batch, with a summary of cost and confidence
python3 scripts/showcase.py

# the webservice, then open http://127.0.0.1:8765
python3 server.py --port 8765
```

The API, if you'd rather not use the page:

```bash
curl "http://127.0.0.1:8765/api/ask?q=Where+can+I+find+rest" | jq
curl "http://127.0.0.1:8765/api/health" | jq
curl -X POST http://127.0.0.1:8765/api/ask \
     -d '{"question": "Is it wrong to be rich?"}' | jq
```

Python 3.11+ is the only requirement — everything is standard library, no
dependencies to install. Needs `OPENROUTER_API_KEY` in the environment or in
`.env` at the project root.

# keyed james bible

Turn a question into a span of King James Version verses.

```
$ python3 ask.py "Is it wrong to eat meat?"

  Romans 14:2   chain confidence ██░░░░░░░░░░░░░░░░░░ 0.064

  Romans 14:2  For one believeth that he may eat all things: another,
               who is weak, eateth herbs.
```

Nothing here generates scripture. The question is *keyed* into the structure of
the Bible — book, chapter, verse — and the passage is then clipped verbatim from
a local copy of the KJV.

## How it works

[**Jev**](https://openrouter.ai/typesafe/jev-1.13) is a System One decision model
from TypeSafe. It does not produce text: it answers typed questions about a
piece of state and returns calibrated probabilities over options you define.

So the question is narrowed one step at a time:

```
  request 1 ── state {question}                 ──►  book  +  extent
  request 2 ── state {question, book}           ──►  chapter
  request 3 ── state {question, book, chapter}  ──►  verse

  ──► then code clips the verses out of the corpus
```

Three requests, not one. Jev answers every question in a request *independently
and in parallel* — one answer never becomes context for another — so a chain has
to run through the shared `state` and be driven by code. The book and extent
questions ride together because neither depends on the other; that is the only
slack in the chain.

See [`docs/how-it-keys.md`](docs/how-it-keys.md) for the full diagram, the
measured behaviour, and two Jev quirks worth knowing (an option keyed `"0"` acts
as a sticky default; option counts cap at 255, and the largest set here is 176 —
Psalm 119).

## Running it

Pure Python standard library, Python 3.11+. No dependencies.

```bash
# build the corpus from the per-book source files (asserts KJV structure)
python3 scripts/build_corpus.py

# ask from the command line
python3 ask.py "Why do bad things happen to good people?"
python3 ask.py --json "What happens after I die?"
python3 ask.py --no-cache "What should I do when I am afraid?"

# the web service, then open http://127.0.0.1:8765
python3 server.py --port 8765
```

With Nix:

```bash
nix run .#              # the web service
nix build .#default     # the package
```

The API:

```bash
curl "http://127.0.0.1:8765/api/ask?q=Where+can+I+find+rest" | jq
curl "http://127.0.0.1:8765/api/health" | jq
```

## The key

An OpenRouter API key is required. The service looks for it in this order:

1. `OPENROUTER_API_KEY_FILE` — a path to a file containing the key
2. `OPENROUTER_API_KEY` — the key itself
3. `.env` at the project root, as `OPENROUTER_API_KEY=...`

The file variant exists because keys handed over by a secret manager are often
bare values with no `KEY=value` wrapper, and systemd's `EnvironmentFile`
*silently ignores* a line with no `=` in it. Reading the file directly avoids
that. Both shapes are accepted, as is a quoted value.

## Cost

Roughly **$0.00017 per question** and ~0.7 s, measured over a mixed batch.

```
1,000 questions   ≈ $0.17
10,000 questions  ≈ $1.68
```

Resolved mappings are cached by question text and model, so asking the same
question again is free. The cache lives at `KJB_CACHE_PATH` if set, otherwise
`data/cache.json`. A cache write failure is swallowed rather than fatal — an
unwritable state directory costs a cache entry, not an answer.

## Corpus

`data/kjv/` holds the public-domain KJV, one JSON file per book, from
[aruljohn/Bible-kjv](https://github.com/aruljohn/Bible-kjv). The King James
Version is public domain in the United States; it remains under perpetual Crown
copyright in the United Kingdom.

`scripts/build_corpus.py` normalizes those files into `data/kjv.json` and
**asserts the canonical structure** — 66 books, 1189 chapters, 31102 verses,
with per-book chapter counts — so a corrupted or partial source tree fails the
Nix build instead of quietly resolving verses incorrectly.

## Layout

```
ask.py               CLI
server.py            HTTP service + JSON API
jev_kjv/
  corpus.py          load and address the KJV by book/chapter/verse
  jev.py             Jev Decisions API client (stdlib HTTP, retries, backoff)
  resolver.py        the book -> chapter -> verse chain, plus the cache
web/                 the page: no build step, no framework
scripts/
  build_corpus.py    corpus normalization + structural assertions
  showcase.py        run a batch of questions with a cost/confidence summary
  experiment_*.py    the harnesses used to measure Jev's behaviour
docs/how-it-keys.md  the design, in diagrams
```

## Deploying

Packaged as a flake exposing `packages.default` and an `apps.default`, and
consumed by [systems-flake](https://github.com/mccartykim/systems-flake) on
`historian` as a host service behind `kjb.kimb.dev`.

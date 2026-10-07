#!/usr/bin/env python3
"""A tiny webservice that turns a question into a span of KJV verses.

    python3 server.py                 # http://127.0.0.1:8765
    python3 server.py --port 8080

Endpoints:
    GET  /                  the page
    GET  /api/ask?q=...     resolve a question (also accepts POST with JSON)
    GET  /api/health        corpus and cache status

Nothing here generates text. Jev returns a probability distribution over the
options at each level of the Bible's structure, and the verse text is clipped
straight out of the local KJV corpus.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from jev_kjv.jev import JevError  # noqa: E402
from jev_kjv.resolver import Resolver  # noqa: E402

WEB_DIR = pathlib.Path(__file__).resolve().parent / "web"

STATIC_ROUTES: dict[str, tuple[str, str]] = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
}

MAX_QUESTION_CHARS = 500

# Sizing for the intended load: a couple of dozen people asking questions at
# once. Each answer costs three Jev calls and takes about 0.7 s, and a request
# holds a slot for that whole time, so concurrency needs to cover simultaneous
# questions, not their rate. The machine itself (24 threads, fast link) can
# absorb far more than this.
DEFAULT_MAX_CONCURRENCY = 20

# The rate limits below are deliberately conservative, and they bound SPEND
# rather than load. Only a genuinely new question costs anything (~$0.00017);
# cached questions and bad requests are free and bypass these limits entirely.
# So the ceiling is what a runaway script pointed at the endpoint could bill:
#
#   60 questions/min x $0.00017  ~=  $0.010/min  ~=  $0.61/hour
#
# i.e. roughly $15/day if something sustained it around the clock. These numbers
# are not what stops a determined client — anyone can stay under any rate limit —
# they stop an unattended script from quietly running up a bill. The real
# backstop is a spend limit on the OpenRouter key itself.
DEFAULT_BURST = 10  # questions one client may fire back to back
DEFAULT_PER_MINUTE = 10.0  # sustained per client
DEFAULT_GLOBAL_BURST = 30  # questions the whole service may absorb at once
DEFAULT_GLOBAL_PER_MINUTE = 60.0  # sustained across all clients


class TokenBucket:
    """Token buckets keyed by client, refilled continuously.

    A bucket starts full, so a burst up to `burst` passes immediately and the
    sustained rate settles at `per_minute`. Thread-safe: the server is threaded,
    so every read-modify-write of a bucket happens under one lock.
    """

    def __init__(self, burst: float, per_minute: float, max_keys: int = 4096) -> None:
        self.burst = float(burst)
        self.rate = per_minute / 60.0
        self.max_keys = max_keys
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def take(self, key: str) -> tuple[bool, float]:
        """Spend one token. Returns (allowed, seconds_until_retry)."""
        now = time.monotonic()
        with self._lock:
            tokens, last = self._buckets.get(key, (self.burst, now))
            tokens = min(self.burst, tokens + (now - last) * self.rate)

            if tokens >= 1.0:
                self._buckets[key] = (tokens - 1.0, now)
                return True, 0.0

            self._buckets[key] = (tokens, now)
            wait = (1.0 - tokens) / self.rate if self.rate > 0 else 60.0
            self._maybe_prune(now)
            return False, wait

    def _maybe_prune(self, now: float) -> None:
        """Drop buckets that have refilled, so a flood of client IPs cannot grow
        the table without bound. Called while the lock is held."""
        if len(self._buckets) <= self.max_keys:
            return
        idle = self.burst / self.rate if self.rate > 0 else 60.0
        for key in [k for k, (_, last) in self._buckets.items() if now - last > idle]:
            del self._buckets[key]


def client_ip(handler: BaseHTTPRequestHandler) -> str:
    """Best-effort client address.

    Behind the reverse proxy every request arrives from Caddy on loopback, so
    the socket address would collapse all clients into one bucket and make the
    per-client limit meaningless. Caddy sets X-Forwarded-For, whose first entry
    is the original client.
    """
    forwarded = handler.headers.get("X-Forwarded-For")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    return handler.client_address[0]


class Service(ThreadingHTTPServer):
    """HTTP server that owns one resolver and bounds concurrent Jev calls."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address,
        resolver: Resolver,
        max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
        burst: float = DEFAULT_BURST,
        per_minute: float = DEFAULT_PER_MINUTE,
        global_burst: float = DEFAULT_GLOBAL_BURST,
        global_per_minute: float = DEFAULT_GLOBAL_PER_MINUTE,
    ):
        super().__init__(address, Handler)
        self.resolver = resolver
        self.gate = threading.BoundedSemaphore(max_concurrency)
        self.per_client = TokenBucket(burst, per_minute)
        self.global_limiter = TokenBucket(global_burst, global_per_minute)


class Handler(BaseHTTPRequestHandler):
    server_version = "keyed-james-bible/0.1"
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------ utilities

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self._send(status, body, "application/json; charset=utf-8")

    def _error(self, status: int, message: str, hint: str | None = None) -> None:
        payload: dict = {"error": message}
        if hint:
            payload["hint"] = hint
        self._send_json(status, payload)

    def _too_many(self, retry_after: float, message: str) -> None:
        """Answer 429 with a Retry-After, so a client knows when to come back."""
        seconds = max(1, int(retry_after + 0.5))
        body = json.dumps(
            {
                "error": message,
                "retry_after": seconds,
                "hint": f"try again in about {seconds}s",
            },
            ensure_ascii=False,
        ).encode()
        self.send_response(429)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Retry-After", str(seconds))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write(f"{self.address_string()} {fmt % args}\n")

    # --------------------------------------------------------------- routes

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path in STATIC_ROUTES:
            return self._serve_static(STATIC_ROUTES[path])
        if path == "/api/health":
            return self._health()
        if path == "/api/ask":
            params = urllib.parse.parse_qs(parsed.query)
            return self._ask(
                question=(params.get("q") or params.get("question") or [""])[0],
                span=params.get("span", [None])[0],
                fresh=params.get("fresh", ["0"])[0] in {"1", "true", "yes"},
            )
        return self._error(404, f"no route for {path}")

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.rstrip("/") != "/api/ask":
            return self._error(404, f"no route for {parsed.path}")

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._error(400, "invalid Content-Length")
        if length > 64 * 1024:
            return self._error(413, "request body too large")

        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._error(400, "request body is not valid JSON")

        return self._ask(
            question=str(payload.get("q") or payload.get("question") or ""),
            span=payload.get("span"),
            fresh=bool(payload.get("fresh")),
        )

    def _serve_static(self, route: tuple[str, str]) -> None:
        filename, content_type = route
        path = WEB_DIR / filename
        if not path.is_file():
            return self._error(500, f"missing web asset: {filename}")
        self._send(200, path.read_bytes(), content_type)

    def _health(self) -> None:
        resolver = self.server.resolver
        corpus = resolver.corpus
        self._send_json(200, {
            "ok": True,
            "model": resolver.client.model,
            "books": len(corpus.books),
            "chapters": corpus.chapter_total,
            "verses": corpus.verse_total,
            "cached_questions": len(resolver._read_cache()),
        })

    def _ask(self, question: str, span: object, fresh: bool) -> None:
        question = " ".join(str(question).split())
        if not question:
            return self._error(400, "missing question", "send ?q=your+question")
        if len(question) > MAX_QUESTION_CHARS:
            return self._error(
                400, f"question is too long (max {MAX_QUESTION_CHARS} characters)"
            )

        # Rate limits apply only to work that can cost money. Cached answers and
        # bad requests are free, so charging tokens for them would penalise
        # legitimate repeat use.
        if not fresh:
            allowed, retry_after = self.server.global_limiter.take("global")
            if not allowed:
                return self._too_many(retry_after, "the service is busy right now")
            allowed, retry_after = self.server.per_client.take(client_ip(self))
            if not allowed:
                return self._too_many(retry_after, "too many questions at once")

        span_override: int | None = None
        if span not in (None, ""):
            try:
                span_override = int(span)
            except (TypeError, ValueError):
                return self._error(400, "span must be a whole number of verses")
            if not 1 <= span_override <= 12:
                return self._error(400, "span must be between 1 and 12 verses")

        acquired = self.server.gate.acquire(timeout=60)
        if not acquired:
            return self._error(503, "server busy, try again shortly")
        try:
            reading = self.server.resolver.resolve(question, use_cache=not fresh)
            payload = reading.as_dict()
            if span_override and reading.verses:
                first = reading.verses[0]
                corpus = self.server.resolver.corpus
                re_read = corpus.span(
                    first.book,
                    first.chapter,
                    first.verse,
                    first.verse + span_override - 1,
                )
                payload["verses"] = [v.as_dict() for v in re_read]
                last = re_read[-1]
                payload["reference"] = (
                    first.reference
                    if last.verse == first.verse
                    else f"{first.book} {first.chapter}:{first.verse}-{last.verse}"
                )
                payload["span_override"] = span_override
            return self._send_json(200, payload)
        except JevError as exc:
            # JevError messages are ours and describe the failure usefully, so
            # they are returned as-is. The key-file path is the one exception:
            # it can be a genuine configuration problem worth naming.
            hint = None
            if "OPENROUTER_API_KEY" in str(exc):
                hint = (
                    "set OPENROUTER_API_KEY or OPENROUTER_API_KEY_FILE, or put "
                    "OPENROUTER_API_KEY=... in .env at the project root"
                )
            return self._error(502, str(exc), hint)
        except (KeyError, ValueError) as exc:
            # These are raised by our own corpus/argument handling and are safe to
            # quote: they name a book, chapter, or verse, nothing internal.
            return self._error(500, f"could not resolve the question: {exc}")
        except Exception as exc:  # noqa: BLE001 - report, do not crash the server
            # Anything else is unexpected: a bug, or a failure in a library. The
            # raw repr can carry the store path the process runs from, which is
            # an internal detail worth nothing to a caller, so it goes to the log
            # and the client gets a generic message.
            traceback.print_exc()
            return self._error(500, "something went wrong handling that question")
        finally:
            self.server.gate.release()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1", help="default 127.0.0.1")
    parser.add_argument("--port", type=int, default=8765, help="default 8765")
    parser.add_argument("--no-cache", action="store_true", help="do not persist mappings")
    parser.add_argument("--model", default=None, help="override the Jev model id")
    parser.add_argument(
        "--max-concurrency",
        type=int,
        default=DEFAULT_MAX_CONCURRENCY,
        help=f"simultaneous Jev-backed questions (default {DEFAULT_MAX_CONCURRENCY})",
    )
    parser.add_argument(
        "--burst",
        type=int,
        default=DEFAULT_BURST,
        help=f"questions one client may fire back to back (default {DEFAULT_BURST})",
    )
    parser.add_argument(
        "--per-minute",
        type=float,
        default=DEFAULT_PER_MINUTE,
        help=f"sustained questions per minute per client (default {DEFAULT_PER_MINUTE:.0f})",
    )
    parser.add_argument(
        "--global-burst",
        type=int,
        default=DEFAULT_GLOBAL_BURST,
        help=f"questions the whole service absorbs at once "
        f"(default {DEFAULT_GLOBAL_BURST})",
    )
    parser.add_argument(
        "--global-per-minute",
        type=float,
        default=DEFAULT_GLOBAL_PER_MINUTE,
        help=f"sustained questions per minute across all clients "
        f"(default {DEFAULT_GLOBAL_PER_MINUTE:.0f})",
    )
    args = parser.parse_args(argv)

    try:
        resolver = Resolver(use_cache=not args.no_cache)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.model:
        from jev_kjv.jev import JevClient

        resolver.client = JevClient(model=args.model)

    try:
        resolver.client.api_key
    except JevError as exc:
        print(f"warning: {exc}", file=sys.stderr)
        print("         the page will load, but questions will fail.", file=sys.stderr)

    corpus = resolver.corpus
    server = Service(
        (args.host, args.port),
        resolver,
        max_concurrency=args.max_concurrency,
        burst=args.burst,
        per_minute=args.per_minute,
        global_burst=args.global_burst,
        global_per_minute=args.global_per_minute,
    )
    print(f"keyed james bible on http://{args.host}:{args.port}")
    print(
        f"  corpus: {len(corpus.books)} books, {corpus.chapter_total} chapters, "
        f"{corpus.verse_total} verses"
    )
    print(f"  model:  {resolver.client.model}")
    print(
        f"  limits: {args.max_concurrency} concurrent · "
        f"{args.burst}/{args.per_minute:.0f} per client · "
        f"{args.global_burst}/{args.global_per_minute:.0f} overall "
        f"(burst/per-minute; cached questions are free)"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

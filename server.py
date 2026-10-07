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
import pathlib
import sys
import threading
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


class Service(ThreadingHTTPServer):
    """HTTP server that owns one resolver and bounds concurrent Jev calls."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, resolver: Resolver, max_concurrency: int = 8):
        super().__init__(address, Handler)
        self.resolver = resolver
        self.gate = threading.BoundedSemaphore(max_concurrency)


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
            hint = None
            if "OPENROUTER_API_KEY" in str(exc):
                hint = (
                    "set OPENROUTER_API_KEY or OPENROUTER_API_KEY_FILE, or put "
                    "OPENROUTER_API_KEY=... in .env at the project root"
                )
            return self._error(502, str(exc), hint)
        except (KeyError, ValueError) as exc:
            return self._error(500, f"could not resolve the question: {exc}")
        except Exception as exc:  # noqa: BLE001 - report, do not crash the server
            return self._error(500, f"unexpected error: {exc!r}")
        finally:
            self.server.gate.release()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1", help="default 127.0.0.1")
    parser.add_argument("--port", type=int, default=8765, help="default 8765")
    parser.add_argument("--no-cache", action="store_true", help="do not persist mappings")
    parser.add_argument("--model", default=None, help="override the Jev model id")
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
    server = Service((args.host, args.port), resolver)
    print(f"keyed james bible on http://{args.host}:{args.port}")
    print(
        f"  corpus: {len(corpus.books)} books, {corpus.chapter_total} chapters, "
        f"{corpus.verse_total} verses"
    )
    print(f"  model:  {resolver.client.model}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

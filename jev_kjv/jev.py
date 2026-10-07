"""Thin client for the Jev Decisions API, as exposed through OpenRouter.

Jev answers typed questions about a piece of state and returns calibrated
probabilities rather than generated text. Only the Choice primitive is used
here: each call names a set of options and gets back the selected option,
a probability for every option, and a confidence value.

Endpoint and request/response shapes follow
https://openrouter.ai/docs/guides/community/jev-tutorial
"""

from __future__ import annotations

import json
import os
import pathlib
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

DEFAULT_BASE_URL = "https://openrouter.ai/api/alpha/decisions"
DEFAULT_MODEL = "typesafe/jev-1.13"

# Jev's context window is 32,000 tokens shared by the state and the questions.
# A verse-level Choice sends a chapter's worth of option descriptions, so the
# caller is responsible for keeping chapter selection and verse selection in
# separate requests rather than concatenating them.
CONTEXT_LIMIT_TOKENS = 32_000

_RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}


class JevError(RuntimeError):
    """Raised when the Decisions API cannot be reached or returns an error."""


@dataclass(frozen=True)
class ChoiceAnswer:
    """The answer to a single Choice question."""

    choice: str
    confidence: float
    probabilities: dict[str, float]

    def ranked(self) -> list[tuple[str, float]]:
        """Options sorted by probability, most likely first."""
        return sorted(self.probabilities.items(), key=lambda kv: (-kv[1], kv[0]))


@dataclass(frozen=True)
class NoulAnswer:
    """The answer to a single Noul question: the probability that it is true.

    Unlike Choice, there is no selected option and no confidence value — a Noul
    is one number in [0, 1]. Values near 0.5 mean the model had no lean.
    """

    noul: float

    @property
    def is_yes(self) -> bool:
        """Whether the answer leans yes, i.e. above even odds."""
        return self.noul > 0.5

    @property
    def lean(self) -> float:
        """Distance from even odds: 0 at a coin flip, 1 at certainty."""
        return abs(self.noul - 0.5) * 2


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int
    cost: float


@dataclass(frozen=True)
class DecisionResult:
    answers: dict[str, ChoiceAnswer | NoulAnswer]
    usage: Usage
    model: str
    request_id: str
    latency_ms: int
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def choice(self, question_id: str) -> ChoiceAnswer:
        """The Choice answer for a question id, or raise if it is not a Choice."""
        answer = self.answers.get(question_id)
        if answer is None:
            raise JevError(
                f"no answer for question {question_id!r}; got {sorted(self.answers)}"
            )
        if not isinstance(answer, ChoiceAnswer):
            raise JevError(f"question {question_id!r} is not a Choice answer")
        return answer

    def noul(self, question_id: str) -> NoulAnswer:
        """The Noul answer for a question id, or raise if it is not a Noul."""
        answer = self.answers.get(question_id)
        if answer is None:
            raise JevError(
                f"no answer for question {question_id!r}; got {sorted(self.answers)}"
            )
        if not isinstance(answer, NoulAnswer):
            raise JevError(f"question {question_id!r} is not a Noul answer")
        return answer


def _load_key_from_dotenv() -> str | None:
    """Read OPENROUTER_API_KEY from a .env file next to the project root."""
    env_path = pathlib.Path(__file__).resolve().parent.parent / ".env"
    if not env_path.is_file():
        return None
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        if name.strip() == "OPENROUTER_API_KEY":
            return value.strip().strip("'\"")
    return None


def find_api_key() -> str:
    """Locate the OpenRouter key.

    Order: OPENROUTER_API_KEY_FILE, then OPENROUTER_API_KEY, then .env.

    The file variant exists because a key handed over by agenix is often a bare
    value with no `KEY=value` wrapper, and systemd's EnvironmentFile silently
    ignores a line with no `=` in it. Reading the file ourselves sidesteps that
    entirely, and matches how media-classifier takes its key on historian
    (`jevApiKeyFile`).
    """
    key_file = os.environ.get("OPENROUTER_API_KEY_FILE")
    if key_file:
        path = pathlib.Path(key_file)
        if not path.is_file():
            raise JevError(f"OPENROUTER_API_KEY_FILE points at {key_file}, which does not exist")
        try:
            value = path.read_text()
        except OSError as exc:
            raise JevError(f"could not read {key_file}: {exc}") from exc
        value = _extract_key(value)
        if not value:
            raise JevError(f"{key_file} contains no usable key")
        return value

    key = os.environ.get("OPENROUTER_API_KEY")
    if key:
        return key.strip()

    key = _load_key_from_dotenv()
    if key:
        return key

    raise JevError(
        "no OpenRouter key found. Set OPENROUTER_API_KEY_FILE or "
        "OPENROUTER_API_KEY, or put OPENROUTER_API_KEY=... in a .env file at "
        "the project root."
    )


def _extract_key(text: str) -> str:
    """Pull a key out of a secret file, tolerating both shapes.

    Accepts a bare value, a `KEY=value` line (as agenix EnvironmentFiles use),
    or a value wrapped in quotes.
    """
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("OPENROUTER_API_KEY="):
            line = line.split("=", 1)[1]
        return line.strip().strip("'\"")
    return ""


class JevClient:
    """Client for Choice questions against the Jev Decisions API."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 120.0,
        max_retries: int = 4,
    ) -> None:
        self.api_key = api_key or find_api_key()
        self.model = model
        self.base_url = base_url
        self.timeout = timeout
        self.max_retries = max_retries

    def ask(
        self,
        state: Any,
        questions: dict[str, dict[str, Any]],
    ) -> DecisionResult:
        """Send a batch of questions about one state and parse the answers.

        Every question in a batch is answered independently and in parallel;
        no question can see another's answer. That makes a batch useless for
        a chain where a later step depends on an earlier one, so chained
        selection issues one request per level.
        """
        payload = {"model": self.model, "state": state, "questions": questions}
        body = json.dumps(payload).encode()

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            started = time.monotonic()
            request = urllib.request.Request(
                self.base_url,
                data=body,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    raw = json.loads(response.read())
                latency_ms = int((time.monotonic() - started) * 1000)
                return self._parse(raw, latency_ms)
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode(errors="replace")[:500]
                last_error = JevError(f"HTTP {exc.code} from Jev: {detail}")
                if exc.code not in _RETRYABLE_STATUS or attempt == self.max_retries:
                    raise last_error from exc
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = JevError(f"Jev request failed: {exc}")
                if attempt == self.max_retries:
                    raise last_error from exc

            # Exponential backoff with jitter: 0.5s, 1s, 2s, 4s ...
            delay = 0.5 * (2**attempt) * (1 + random.random() * 0.25)
            time.sleep(delay)

        raise last_error or JevError("Jev request failed")

    def choice(
        self,
        state: Any,
        instructions: str,
        criteria: dict[str, Any],
        question_id: str = "choice",
    ) -> DecisionResult:
        """Ask a single Choice question. Returns the full result for usage data."""
        return self.ask(
            state,
            {
                question_id: {
                    "type": "choice",
                    "instructions": instructions,
                    "criteria": criteria,
                }
            },
        )

    def noul(
        self,
        state: Any,
        instructions: str,
        criteria: dict[str, str] | None = None,
        question_id: str = "noul",
    ) -> DecisionResult:
        """Ask a single yes/no question. Returns the full result for usage data.

        `criteria` describes what would make the answer true and false. It is
        optional in the API but worth giving: it is where a vague question gets
        pinned down.
        """
        question: dict[str, Any] = {"type": "noul", "instructions": instructions}
        if criteria:
            question["criteria"] = criteria
        return self.ask(state, {question_id: question})

    @staticmethod
    def _parse(raw: dict[str, Any], latency_ms: int) -> DecisionResult:
        answers: dict[str, ChoiceAnswer | NoulAnswer] = {}
        for name, answer in raw.get("answers", {}).items():
            kind = answer.get("type")
            if kind == "choice":
                answers[name] = ChoiceAnswer(
                    choice=answer["choice"],
                    confidence=float(answer.get("confidence", 0.0)),
                    probabilities={
                        k: float(v) for k, v in answer.get("probabilities", {}).items()
                    },
                )
            elif kind == "noul":
                answers[name] = NoulAnswer(noul=float(answer["noul"]))
            # Score answers are not used by this project and are skipped rather
            # than guessed at.

        usage = raw.get("usage", {})
        return DecisionResult(
            answers=answers,
            usage=Usage(
                input_tokens=int(usage.get("input_tokens", 0)),
                output_tokens=int(usage.get("output_tokens", 0)),
                cost=float(usage.get("cost", 0.0)),
            ),
            model=raw.get("model", ""),
            request_id=raw.get("id", ""),
            latency_ms=latency_ms,
            raw=raw,
        )

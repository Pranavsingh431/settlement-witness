"""Calling a hosted model, against the shadow corpus and nothing else.

This is the first thing in the project that sends anything to a third party, so
what it may send is the design rather than a detail.

**It receives corpus presentation fields and a fixed instruction.** Nothing
else. No canonical source fact, no payload hash, no money, no CSV, no document
text, no scenario label, no expected action, no oracle, and nothing from the
application database. The corpus is generated in memory and every identifier in
it is a digest of a fixed seed, so what leaves the process is a list of opaque
tokens and their opaque references.

**It can only receive a selection back.** The response goes through the same
`RawLinkSelection` parsing and the same deterministic validation as a fixture's
does. There is no path from here to a decision, a run, or the database, and the
adapter has no access to any of them.

**Nothing is repaired.** A response that is not JSON, is JSON in the wrong
shape, carries a field it may not, names a record that was not offered, or
arrives too late is a typed failure or a rejection. Markdown fences are not
stripped, extra keys are not trimmed, identifiers are not guessed at, and
nothing is retried. A repaired answer is partly the model's and partly ours, and
a report over those cannot say which.

**It may only ask about pages it was authorised to ask about.** A provider is
built with an allow-list of request fingerprints and refuses anything outside
it before a header exists or a socket is opened. The command that uses this
derives that list from `build_corpus()`, so corpus-only is a property of the
adapter rather than a property of which arguments the command happens to
offer.

**It reads a bounded number of bytes.** The response is streamed and abandoned
at the budget plus whatever chunk crossed it. A host that answers with a
gigabyte costs a bounded read, whether or not it declared the size honestly.

**The key is never written down.** It is held in a `SecretStr`, read once when
the request is built, and appears in no repr, no serialisation, no log, no
exception and no artifact. A test asserts that across every configuration and
failure path. The endpoint is held to the same standard: a base URL carrying
user-info credentials, a query string or a fragment is refused, because those
are places a token is put and the endpoint is copied into a run receipt.
"""

import json
from collections.abc import Mapping
from typing import Any, Final, Self
from urllib.parse import urlparse

import httpx2
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from app.ai.candidates import LinkProposalRequest
from app.ai.proposals import ProviderIdentity
from app.ai.provider import FailureKind, ProviderFailure, ProviderResult

ADAPTER_NAME: Final = "openai-compatible"
"""What this adapter is, recorded as the provider name.

Vendor neutral: it speaks the chat-completions shape that several hosts serve,
and the host is whatever `SETTLEMENT_WITNESS_AI_BASE_URL` names."""

TEMPERATURE: Final = 0.0
"""Decoding temperature, recorded in the run provenance.

Zero, because a run should vary as little as the host allows. It does **not**
make a hosted model reproducible: batching, routing, hardware and silent model
updates all move an answer, and no setting available here controls any of
them."""

LOCAL_HOSTS: Final = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})
"""Hosts that may be reached over plain HTTP.

For a fake server on the same machine during development. Everything else must
be HTTPS, because the request carries an API key in a header."""

_ENV: Final = {
    "base_url": "SETTLEMENT_WITNESS_AI_BASE_URL",
    "api_key": "SETTLEMENT_WITNESS_AI_API_KEY",
    "model": "SETTLEMENT_WITNESS_AI_MODEL",
    "timeout_seconds": "SETTLEMENT_WITNESS_AI_TIMEOUT_SECONDS",
    "max_response_bytes": "SETTLEMENT_WITNESS_AI_MAX_RESPONSE_BYTES",
    "max_requests": "SETTLEMENT_WITNESS_AI_MAX_REQUESTS",
}

INSTRUCTION: Final = (
    "You are given one page of candidate source records for a single settlement "
    "line. Select the candidates that belong to that line, using only the "
    "reference fields shown. "
    "Reply with JSON only, matching exactly this shape and no other keys: "
    '{"outcome": "PROPOSE", "selected_source_record_ids": ["<id>"]} '
    'or {"outcome": "ABSTAIN", "selected_source_record_ids": []}. '
    "Select only source_record_id values that appear in this page. "
    "Abstain when the fields shown do not identify which candidates belong. "
    "Do not explain, do not add fields, and do not wrap the JSON in anything."
)
"""The whole of what the model is told.

Fixed, and never combined with data. The request is sent as a separate JSON
message, so a hostile value inside a record cannot be read as part of an
instruction: there is no string concatenation for it to escape from. That is a
structural precaution and not a solution to prompt injection, which JSON
formatting does not solve."""

RESPONSE_SCHEMA: Final[Mapping[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["outcome", "selected_source_record_ids"],
    "properties": {
        "outcome": {"type": "string", "enum": ["PROPOSE", "ABSTAIN"]},
        "selected_source_record_ids": {"type": "array", "items": {"type": "string"}},
    },
}
"""Asked for as structured output where the host supports it.

A convenience, never a guarantee. Every response still goes through the same
parsing and validation as one from a host that ignored this entirely."""


class MissingConfiguration(RuntimeError):
    """Raised when the environment does not describe a usable provider.

    Names the variables that were missing or wrong and never their values, so
    that a message about a bad key cannot contain the key.

    Deliberately not a `ValueError`. Pydantic wraps a `ValueError` raised inside
    a validator into a `ValidationError` whose text echoes the input it was
    given, and the input here is an endpoint that may carry a password. A
    `RuntimeError` propagates out of a validator unchanged, so the only text
    anyone sees is the text written here.
    """


class NothingAuthorised(ValueError):
    """Raised when a provider is built with an empty allow-list.

    There is no permissive default. A provider that could ask about anything is
    exactly the provider this design does not have, so an empty list is a
    programming error rather than a permissive configuration.
    """


class HostedProviderConfig(BaseModel):
    """Everything needed to call a host, and nothing that identifies a person.

    There is no default endpoint, no default model and no default key. A
    misconfigured run must fail rather than quietly reach somebody's default.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    base_url: str = Field(min_length=1)
    api_key: SecretStr
    """Held as a secret, so it is redacted in every repr and serialisation.

    `model_dump()` on this config yields `SecretStr('**********')` rather than
    the key, which is what keeps it out of a receipt written by accident."""

    model: str = Field(min_length=1, max_length=200)
    timeout_seconds: float = Field(gt=0, le=120)
    max_response_bytes: int = Field(gt=0, le=1_000_000)
    max_requests: int = Field(gt=0, le=10_000)

    @model_validator(mode="after")
    def _endpoint_is_safe(self) -> Self:
        """Refuse an endpoint that would send a secret in the clear or carry one.

        Plain HTTP is allowed only to the local machine, which is for a fake
        server during development. Anywhere else, a header carrying an API key
        must be encrypted.

        A base URL is also refused when it carries user-info credentials, a
        query string or a fragment. Those are the three places a token gets put
        when somebody is handed a "just use this URL" endpoint, and this URL is
        copied verbatim into the provenance of every run receipt. There is no
        stripping: a URL that carries a secret is a configuration mistake, and
        quietly removing the secret would leave the operator believing they had
        configured something they had not.

        Raises:
            MissingConfiguration: Naming the variable and the problem. Never the
                value, because the value is the part that might be a secret.
        """
        try:
            parsed = urlparse(self.base_url)
        except ValueError as error:
            # urlparse refuses a malformed bracketed IPv6 host. Re-raised here
            # so the only message anyone sees is one written in this file.
            message = f"{_ENV['base_url']} is not a URL that can be parsed"
            raise MissingConfiguration(message) from error
        if parsed.scheme not in {"http", "https"}:
            message = f"{_ENV['base_url']} must be an http or https URL"
            raise MissingConfiguration(message)
        if not parsed.hostname:
            message = f"{_ENV['base_url']} names no host"
            raise MissingConfiguration(message)
        if parsed.scheme == "http" and parsed.hostname not in LOCAL_HOSTS:
            message = (
                f"{_ENV['base_url']} must use https unless it names a local test "
                f"endpoint; {sorted(LOCAL_HOSTS)} may use http"
            )
            raise MissingConfiguration(message)
        for part, description in (
            (parsed.username or parsed.password, "user-info credentials"),
            (parsed.query, "a query string"),
            (parsed.fragment, "a fragment"),
        ):
            if part:
                message = (
                    f"{_ENV['base_url']} must not carry {description}, because a "
                    f"secret put there would be copied into every run receipt. "
                    f"Give the endpoint alone and put the key in "
                    f"{_ENV['api_key']}."
                )
                raise MissingConfiguration(message)
        return self

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> "HostedProviderConfig":
        """Read the configuration, or say exactly which variables are wrong.

        Args:
            environment: The variables to read, passed in rather than taken from
                the process so a test never depends on ambient state.

        Returns:
            A validated configuration.

        Raises:
            MissingConfiguration: When a variable is absent or unusable. The
                message names variables and never values.
        """
        missing = sorted(name for name in _ENV.values() if not environment.get(name))
        if missing:
            message = f"these environment variables are not set: {missing}"
            raise MissingConfiguration(message)

        numbers: dict[str, float] = {}
        for field in ("timeout_seconds", "max_response_bytes", "max_requests"):
            raw = environment[_ENV[field]]
            try:
                numbers[field] = float(raw)
            except ValueError as error:
                message = f"{_ENV[field]} must be a number"
                raise MissingConfiguration(message) from error

        try:
            return cls(
                base_url=environment[_ENV["base_url"]].rstrip("/"),
                api_key=SecretStr(environment[_ENV["api_key"]]),
                model=environment[_ENV["model"]],
                timeout_seconds=numbers["timeout_seconds"],
                max_response_bytes=int(numbers["max_response_bytes"]),
                max_requests=int(numbers["max_requests"]),
            )
        except ValueError as error:
            # Pydantic's message names fields and their constraints. It cannot
            # name the key, because the key is a SecretStr.
            message = f"the provider configuration is not usable: {error}"
            raise MissingConfiguration(message) from error

    def provenance(self) -> dict[str, object]:
        """Return the non-secret settings, for a run receipt.

        Every field except the key. The key is not omitted by remembering to
        omit it; it is a `SecretStr` and there is no branch here that could
        include it.
        """
        return {
            "base_url": self.base_url,
            "model": self.model,
            "temperature": TEMPERATURE,
            "timeout_seconds": self.timeout_seconds,
            "max_response_bytes": self.max_response_bytes,
            "max_requests": self.max_requests,
            "structured_output_requested": True,
        }


def presentation_payload(request: LinkProposalRequest) -> dict[str, object]:
    """Return exactly what is sent to the host about one page.

    Presentation fields only. The subject's shown references, the page position,
    and each candidate as it is rendered. Deliberately not the whole request:
    the snapshot and environment fingerprints are server-owned digests that a
    model has no use for, and sending them would be sending more than the task
    needs for no reason.

    Nothing canonical, nothing private, nothing from the database.
    """
    return {
        "settlement_line": {
            "id": request.subject_settlement_line_id,
            "payment_reference": request.subject_payment_id,
            "payout_reference": request.subject_payout_id,
        },
        "page": {"ordinal": request.page_ordinal, "of": request.page_count},
        "candidates": [
            {
                "source_record_id": candidate.source_record_id,
                "record_type": candidate.record_type.value,
                "payment_reference": candidate.payment_id,
                "payout_reference": candidate.payout_id,
                "event_type": candidate.event_type,
                "occurred_at": candidate.occurred_at,
            }
            for candidate in request.candidates
        ],
    }


class HostedLinkProposalProvider:
    """An OpenAI-compatible chat-completions client, bounded on every axis.

    One request per page, no conversation, no tools, no history, no follow-up.
    A budget on how many requests a run may make, a timeout on each, and a byte
    budget on each response.

    Not thread safe, and not meant to be: a run asks one page at a time so that
    the request count is exact and a budget cannot be overshot by a race.
    """

    def __init__(
        self,
        config: HostedProviderConfig,
        *,
        authorised_requests: frozenset[str],
        transport: httpx2.BaseTransport | None = None,
    ) -> None:
        """Build a client for one run.

        Args:
            config: A validated configuration. Validation happens before this,
                so a client is never constructed against a bad endpoint.
            authorised_requests: The `request_fingerprint` of every page this
                provider may ask about. Required and keyword only, because a
                provider that will accept any page is the one thing this class
                must not be able to become by accident. Immutable, so a caller
                cannot widen it after construction.
            transport: Injected in tests so nothing reaches the network. In a
                live run this is None and httpx opens real connections.

        Raises:
            NothingAuthorised: When the allow-list is empty.
        """
        if not authorised_requests:
            message = (
                "a hosted provider needs the fingerprints of the pages it may "
                "ask about; there is no permissive default"
            )
            raise NothingAuthorised(message)
        self._config = config
        self._authorised = authorised_requests
        self._client = httpx2.Client(
            timeout=config.timeout_seconds,
            transport=transport,
            follow_redirects=False,
        )
        self._requests_made = 0
        self._typed_failures: dict[str, int] = {}

    @property
    def identity(self) -> ProviderIdentity:
        """Return what this adapter is and which model it was pointed at."""
        return ProviderIdentity(name=ADAPTER_NAME, version=self._config.model)

    @property
    def requests_made(self) -> int:
        """Return how many requests this run has sent."""
        return self._requests_made

    @property
    def typed_failure_counts(self) -> dict[str, int]:
        """Return how many pages ended in each `FailureKind`, by name.

        The adapter knows why each page failed and the shared shadow report does
        not: it records one generic rejection for every provider that did not
        answer, which is the right thing for a report that also scores fixtures.
        Kept here so a live receipt can say whether a run was rate limited or
        could not reach the host at all, which are the same word in the report
        and very different problems.

        Bounded by construction. There are as many keys as there are members of
        `FailureKind`, whatever a host does, and each value is a count. No
        response body, no header, no status text and no provider prose is
        counted, stored or reachable from here.
        """
        return dict(sorted(self._typed_failures.items()))

    def _failed(self, kind: FailureKind) -> ProviderFailure:
        """Count one typed failure and return it."""
        self._typed_failures[kind.value] = self._typed_failures.get(kind.value, 0) + 1
        return ProviderFailure(kind=kind)

    def close(self) -> None:
        """Release the connection pool."""
        self._client.close()

    def __enter__(self) -> "HostedLinkProposalProvider":
        """Return the provider, so a run can be a `with` block."""
        return self

    def __exit__(self, *_: object) -> None:
        """Close the client however the run ended."""
        self.close()

    def propose(self, request: LinkProposalRequest) -> ProviderResult:
        """Ask the host about one page.

        Returns whatever the host said, unparsed, for the ordinary validator to
        judge, or a typed failure. Never raises: a run over a corpus should
        record what went wrong on a page and carry on to the next, and an
        exception escaping here would end the run at whichever page happened to
        fail first.

        A page outside the allow-list is refused first, before the budget is
        consulted, before a header exists and before the client is touched. The
        ordering is the point: whether an unauthorised page would have fitted
        the budget is not a question worth asking.

        Args:
            request: The page to ask about.

        Returns:
            The raw parsed content, or a `ProviderFailure`.
        """
        if request.request_fingerprint not in self._authorised:
            return self._failed(FailureKind.REQUEST_NOT_AUTHORIZED)
        if self._requests_made >= self._config.max_requests:
            return self._failed(FailureKind.BUDGET_EXHAUSTED)

        self._requests_made += 1
        try:
            with self._client.stream(
                "POST",
                f"{self._config.base_url}/chat/completions",
                headers={
                    "authorization": f"Bearer {self._config.api_key.get_secret_value()}",
                    "content-type": "application/json",
                },
                json=self._body(request),
            ) as response:
                return self._read(response)
        except httpx2.TimeoutException:
            return self._failed(FailureKind.TIMED_OUT)
        except httpx2.TransportError:
            return self._failed(FailureKind.CONNECTION_FAILED)
        except httpx2.HTTPError:
            return self._failed(FailureKind.RAISED)

    def _body(self, request: LinkProposalRequest) -> dict[str, object]:
        """Return the request body.

        The instruction and the data are separate messages. The page is sent as
        JSON in its own message rather than interpolated into the instruction,
        so there is no sentence for a value inside a record to escape from.
        """
        return {
            "model": self._config.model,
            "temperature": TEMPERATURE,
            "messages": [
                {"role": "system", "content": INSTRUCTION},
                {
                    "role": "user",
                    "content": json.dumps(
                        presentation_payload(request), sort_keys=True, separators=(",", ":")
                    ),
                },
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "link_selection",
                    "strict": True,
                    "schema": RESPONSE_SCHEMA,
                },
            },
        }

    def _read(self, response: httpx2.Response) -> ProviderResult:
        """Turn one streamed HTTP response into a selection or a typed failure.

        Keeps nothing from the response but the parsed selection. Not the body,
        not the headers, not the status text, and not the host's own error
        prose: none of that belongs beside a reconciliation result, and a
        message written by whatever failed is the least trustworthy thing in
        the exchange.

        The response arrives unread. A refusal is returned without its body ever
        being asked for, and a success is read chunk by chunk and abandoned the
        moment it crosses the budget, so the most an oversized answer can cost
        is the budget plus the one chunk that crossed it.
        """
        if response.status_code // 100 != 2:
            # Returned before a single byte of the body is requested. A host
            # that refuses can also be a host that answers with a gigabyte of
            # explanation, and none of it would be read anyway.
            return self._failed(FailureKind.REFUSED_BY_PROVIDER)

        body = self._bounded_body(response)
        if body is None:
            return self._failed(FailureKind.RESPONSE_TOO_LARGE)
        if not body:
            return self._failed(FailureKind.RETURNED_NOTHING)

        try:
            envelope = json.loads(body)
        except ValueError:
            return self._failed(FailureKind.UNREADABLE_RESPONSE)

        content = _content_of(envelope)
        if content is None:
            return self._failed(FailureKind.UNREADABLE_RESPONSE)

        try:
            selection: object = json.loads(content)
        except ValueError:
            # The host answered in its own shape and the model did not answer in
            # ours. Returned as a failure of the exchange rather than repaired:
            # stripping fences or hunting for a JSON object inside prose would
            # be this adapter deciding what the model meant.
            return self._failed(FailureKind.UNREADABLE_RESPONSE)
        return selection

    def _bounded_body(self, response: httpx2.Response) -> bytes | None:
        """Return the body, or None when it passed the budget.

        Two checks, because one of them can be lied to. A `Content-Length` above
        the budget ends it before the body is asked for, which is the cheap case
        and the honest one. A length that is absent, or present and wrong, is
        caught by the streaming loop instead: bytes are taken a chunk at a time
        and the read stops as soon as the buffer passes the budget, so a
        declared size is a convenience and never the thing relied on.

        Nothing survives an abandoned read. The partial buffer goes out of scope
        with the failure and no part of it is parsed, stored or reported.
        """
        budget = self._config.max_response_bytes
        declared = response.headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > budget:
            return None

        body = bytearray()
        for chunk in response.iter_bytes():
            body += chunk
            if len(body) > budget:
                return None
        return bytes(body)


def _content_of(envelope: object) -> str | None:
    """Return the assistant message text from a chat-completions envelope.

    Reads the one shape this adapter understands and refuses anything else,
    rather than searching a response for something that looks like an answer.
    """
    if not isinstance(envelope, dict):
        return None
    choices = envelope.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    message = first.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    return content if isinstance(content, str) else None

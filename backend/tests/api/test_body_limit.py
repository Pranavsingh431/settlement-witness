"""Tests for the request body limit, driven through the ASGI interface.

`TestClient` always sends an honest `Content-Length`, so a test written with it
only ever exercises the cheap header check and would have passed against the
Phase 6 code that these tests exist to correct. The requests here are scripted
`receive` streams instead, which is the only way to send no length at all, or a
false one, and the only way to see how much of a body the server actually took
before it answered.

Two limits are involved and they are not the same number. The request budget
bounds the whole multipart envelope and is enforced before anything parses it.
The file limit bounds the document inside and is enforced exactly. A document
in the gap between them passes the first and is refused by the second.
"""

import json
from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from starlette.types import ASGIApp, Message, Scope

from app.api.body_limit import replaying
from app.config import Settings
from app.main import MULTIPART_OVERHEAD_BYTES, create_app
from app.storage.database import session_factory
from app.storage.repository import ImportReceiptRepository, SourceFactRepository

FILE_LIMIT = 64 * 1024
BUDGET = FILE_LIMIT + MULTIPART_OVERHEAD_BYTES
BOUNDARY = "----settlement-witness-test"
CONTENT_TYPE = f"multipart/form-data; boundary={BOUNDARY}".encode()


def multipart(payload: bytes, *, file_name: str = "document.csv") -> bytes:
    """Return a complete multipart body carrying one file and the two fields."""
    return (
        (
            f"--{BOUNDARY}\r\n"
            f'Content-Disposition: form-data; name="source_system"\r\n\r\nPSP_API\r\n'
            f"--{BOUNDARY}\r\n"
            f'Content-Disposition: form-data; name="record_type"\r\n\r\nPAYOUT\r\n'
            f"--{BOUNDARY}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{file_name}"\r\n'
            f"Content-Type: text/csv\r\n\r\n"
        ).encode()
        + payload
        + f"\r\n--{BOUNDARY}--\r\n".encode()
    )


ENVELOPE_BYTES = len(multipart(b""))
"""How much the envelope adds, so a test can size a body to land where it means to."""


@dataclass
class RawResponse:
    """What one scripted request got back, and what it cost to answer."""

    status: int = 0
    body: bytes = b""
    delivered: int = 0
    """How many body bytes the server actually took from the stream."""

    headers: dict[bytes, bytes] = field(default_factory=dict)

    @property
    def payload(self) -> Any:
        """Return the response body as JSON."""
        return json.loads(self.body)

    @property
    def text(self) -> str:
        """Return the response body as text."""
        return self.body.decode()


async def send_raw(
    app: ASGIApp,
    body: bytes,
    *,
    chunk_size: int = 16 * 1024,
    content_length: int | None = None,
    path: str = "/v1/imports",
    method: str = "POST",
    content_type: bytes = CONTENT_TYPE,
) -> RawResponse:
    """Drive the application directly, one body chunk at a time.

    Args:
        app: The ASGI application.
        body: The complete request body to offer.
        chunk_size: How much to hand over per `http.request` message.
        content_length: The header to declare. None sends no header at all,
            which is what a chunked client does. Any other value is sent
            verbatim, so a test can lie.
        path: The request path.
        method: The request method.
        content_type: The declared content type.

    Returns:
        The status, the body, and how much of the request was consumed.
    """
    headers: list[tuple[bytes, bytes]] = [(b"content-type", content_type)]
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode()))

    result = RawResponse()
    position = 0

    async def receive() -> Message:
        nonlocal position
        chunk = body[position : position + chunk_size]
        position += chunk_size
        result.delivered += len(chunk)
        return {"type": "http.request", "body": chunk, "more_body": position < len(body)}

    async def send(message: Message) -> None:
        if message["type"] == "http.response.start":
            result.status = message["status"]
            result.headers = dict(message.get("headers", []))
        else:
            result.body += message.get("body", b"")

    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "scheme": "http",
        "headers": headers,
        "client": ("127.0.0.1", 4321),
        "server": ("127.0.0.1", 8000),
    }
    await app(scope, receive, send)
    return result


@pytest.fixture
def limited_app(api_engine: Engine) -> ASGIApp:
    """Return an application with a small, easily crossed upload limit."""
    return create_app(Settings(app_env="test", max_upload_bytes=FILE_LIMIT), engine=api_engine)


class StoreView:
    """Counts what an import would have written, for asserting it wrote nothing."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @property
    def receipts(self) -> int:
        """How many import receipts exist."""
        with session_factory(self._engine)() as session:
            return ImportReceiptRepository(session).count()

    @property
    def facts(self) -> int:
        """How many source facts exist."""
        with session_factory(self._engine)() as session:
            return SourceFactRepository(session).count()


@pytest.fixture
def store(api_engine: Engine) -> StoreView:
    """Return a view over the database this application writes to."""
    return StoreView(api_engine)


class TestRefusingAnOversizedRequest:
    """The budget is enforced by counting, not by believing the client."""

    @pytest.mark.anyio
    async def test_an_honest_length_is_refused_without_reading(
        self, limited_app: ASGIApp, store: StoreView
    ) -> None:
        """A client that declares too much is turned away for free."""
        body = multipart(b"x" * (1024 * 1024))

        response = await send_raw(limited_app, body, content_length=len(body))

        assert response.status == 413
        assert response.delivered == 0
        assert store.receipts == 0

    @pytest.mark.anyio
    async def test_no_content_length_is_still_refused(
        self, limited_app: ASGIApp, store: StoreView
    ) -> None:
        """The case the header check cannot see.

        A chunked client sends no length at all. Before this limiter existed the
        whole body reached the multipart parser and was spooled, and the 413 came
        from the endpoint afterwards.
        """
        body = multipart(b"x" * (1024 * 1024))

        response = await send_raw(limited_app, body, content_length=None)

        assert response.status == 413
        assert response.payload["detail"] == {
            "error": "request_too_large",
            "detail": (
                f"the request body is larger than the {BUDGET} byte limit; "
                "no import was processed and no receipt was written"
            ),
        }
        assert store.receipts == 0

    @pytest.mark.anyio
    async def test_a_forged_length_is_still_refused(
        self, limited_app: ASGIApp, store: StoreView
    ) -> None:
        """A client that lies gets the same answer as one that does not."""
        body = multipart(b"x" * (1024 * 1024))

        response = await send_raw(limited_app, body, content_length=100)

        assert response.status == 413
        assert store.receipts == 0

    @pytest.mark.anyio
    @pytest.mark.parametrize("content_length", [None, 100, 0])
    async def test_the_parser_never_sees_the_whole_body(
        self, limited_app: ASGIApp, content_length: int | None
    ) -> None:
        """At most the budget plus the chunk that crossed it is ever taken.

        This is the claim Phase 6 made and did not keep. Asserting the byte
        count is what makes it checkable: a limit that answers 413 after
        consuming four megabytes is not a limit on what the server accepts.
        """
        chunk = 16 * 1024
        body = multipart(b"x" * (1024 * 1024))

        response = await send_raw(
            limited_app, body, chunk_size=chunk, content_length=content_length
        )

        assert response.status == 413
        assert response.delivered <= BUDGET + chunk
        assert response.delivered < len(body)

    @pytest.mark.anyio
    async def test_one_enormous_chunk_is_refused_after_that_chunk(
        self, limited_app: ASGIApp
    ) -> None:
        """A client that sends everything at once is bounded by that one read."""
        body = multipart(b"x" * (4 * 1024 * 1024))

        response = await send_raw(limited_app, body, chunk_size=len(body), content_length=None)

        assert response.status == 413
        assert response.delivered == len(body)

    @pytest.mark.anyio
    async def test_a_body_at_the_budget_is_not_refused_by_the_limiter(
        self, limited_app: ASGIApp
    ) -> None:
        """The budget is a limit, not an off-by-one.

        The document inside is over the file limit, so this is refused, but by
        the exact file check rather than by the request budget. Which of the two
        answered is the thing being asserted.
        """
        payload = b"x" * (BUDGET - ENVELOPE_BYTES)
        body = multipart(payload)
        assert len(body) == BUDGET

        response = await send_raw(limited_app, body, content_length=None)

        assert response.status == 413
        assert response.payload["detail"]["error"] == "document_too_large"

    @pytest.mark.anyio
    async def test_one_byte_over_the_budget_is_refused_by_the_limiter(
        self, limited_app: ASGIApp
    ) -> None:
        """The other side of the same boundary, answered by the other check."""
        body = multipart(b"x" * (BUDGET - ENVELOPE_BYTES + 1))
        assert len(body) == BUDGET + 1

        response = await send_raw(limited_app, body, content_length=None)

        assert response.status == 413
        assert response.payload["detail"]["error"] == "request_too_large"

    @pytest.mark.anyio
    @pytest.mark.parametrize("chunk_size", [1, 7, BUDGET - 1, BUDGET, BUDGET + 1])
    async def test_the_threshold_holds_at_every_chunk_boundary(
        self, limited_app: ASGIApp, chunk_size: int, store: StoreView
    ) -> None:
        """A chunk landing exactly on, before, or across the budget.

        The count is over the running total, not over one message, so where a
        chunk happens to end must not change the answer.
        """
        body = multipart(b"x" * (BUDGET - ENVELOPE_BYTES + 1))

        response = await send_raw(limited_app, body, chunk_size=chunk_size, content_length=None)

        assert response.status == 413
        assert store.receipts == 0

    @pytest.mark.anyio
    async def test_a_chunk_ending_exactly_on_the_budget_is_allowed(
        self, limited_app: ASGIApp
    ) -> None:
        """Reaching the budget is not passing it."""
        body = multipart(b"x" * (BUDGET - ENVELOPE_BYTES))

        response = await send_raw(limited_app, body, chunk_size=BUDGET, content_length=None)

        assert response.delivered == BUDGET
        assert response.payload["detail"]["error"] == "document_too_large"


class TestTheFileLimitIsStillExact:
    """The budget bounds the request. This bounds the document."""

    @pytest.mark.anyio
    async def test_a_document_at_the_limit_is_imported(
        self, limited_app: ASGIApp, store: StoreView
    ) -> None:
        """Exactly `max_upload_bytes`, inside an envelope that fits the budget.

        It is refused as a document because it is not readable CSV, which is the
        correct outcome and proves it reached the import service: a receipt
        exists for it.
        """
        body = multipart(b"x" * FILE_LIMIT)
        assert len(body) <= BUDGET

        response = await send_raw(limited_app, body, content_length=None)

        assert response.status == 201
        assert response.payload["outcome"] == "REJECTED_INVALID"
        assert store.receipts == 1
        assert store.facts == 0

    @pytest.mark.anyio
    async def test_a_document_one_byte_over_the_limit_is_refused(
        self, limited_app: ASGIApp, store: StoreView
    ) -> None:
        """Under the request budget, over the file limit, so the second check answers."""
        body = multipart(b"x" * (FILE_LIMIT + 1))
        assert len(body) <= BUDGET

        response = await send_raw(limited_app, body, content_length=None)

        assert response.status == 413
        assert response.payload["detail"] == {
            "error": "document_too_large",
            "detail": (
                f"the uploaded document is larger than the {FILE_LIMIT} byte limit; "
                "no import was processed and no receipt was written"
            ),
        }
        assert store.receipts == 0
        assert store.facts == 0


class TestANormalUploadIsUnaffected:
    """The limiter must be invisible to a request that fits."""

    @pytest.mark.anyio
    async def test_a_document_under_both_limits_imports(
        self, limited_app: ASGIApp, store: StoreView
    ) -> None:
        """Chunked, with no declared length, and it still works."""
        document = b"provider_event_id,payout_id,merchant_id,net_minor,currency,utr,occurred_at\r\n"
        document += b"po-0001,payout-0001,merch-01,1220500,INR,UTR1,2026-08-21T19:30:00+05:30\r\n"

        response = await send_raw(
            limited_app, multipart(document), chunk_size=64, content_length=None
        )

        assert response.status == 201
        assert response.payload["outcome"] == "ACCEPTED"
        assert store.facts == 1

    def test_a_document_sent_the_ordinary_way_still_imports(self, api_engine: Engine) -> None:
        """Through the client a normal caller would use."""
        with TestClient(create_app(Settings(app_env="test"), engine=api_engine)) as client:
            response = client.post(
                "/v1/imports",
                files={"file": ("payouts.csv", b"not csv", "text/csv")},
                data={"source_system": "PSP_API", "record_type": "PAYOUT"},
            )

        assert response.status_code == 201


class TestOtherRoutesAreNotTouched:
    """Only the upload route has its body counted."""

    @pytest.mark.anyio
    async def test_a_get_to_the_same_path_is_not_limited(self, limited_app: ASGIApp) -> None:
        """Listing receipts has no body to bound."""
        response = await send_raw(
            limited_app, b"", method="GET", path="/v1/imports", content_length=None
        )

        assert response.status == 200

    @pytest.mark.anyio
    async def test_a_post_to_another_path_is_not_limited(self, limited_app: ASGIApp) -> None:
        """Creating a run takes no body, so nothing is buffered for it."""
        response = await send_raw(
            limited_app,
            b"",
            method="POST",
            path="/v1/reconciliation/runs",
            content_length=None,
            content_type=b"application/json",
        )

        assert response.status == 409

    def test_the_read_endpoints_behave_as_before(self, client: TestClient) -> None:
        """Health, runs and receipts, through an application carrying the limiter."""
        assert client.get("/health").status_code == 200
        assert client.get("/v1/imports").status_code == 200
        assert client.post("/v1/reconciliation/runs").status_code == 201

    @pytest.mark.anyio
    async def test_a_large_body_on_an_unlimited_route_is_not_refused(
        self, limited_app: ASGIApp
    ) -> None:
        """The rule is scoped, so another route keeps its own behaviour.

        A body far past the upload budget sent to the run endpoint is ignored
        rather than refused, because that endpoint declares no body at all.
        """
        response = await send_raw(
            limited_app,
            b"x" * (1024 * 1024),
            method="POST",
            path="/v1/reconciliation/runs",
            content_length=None,
            content_type=b"application/json",
        )

        assert response.status == 409


class TestTheRefusalSaysNothingItShouldNot:
    """A 413 explains a limit. It is not a place for anything else."""

    @pytest.fixture
    async def refusal(self, limited_app: ASGIApp) -> RawResponse:
        """Return the response to an oversized upload carrying recognisable content."""
        secret = b"merchant-secret-row,999999,INR\n" * 40_000
        return await send_raw(limited_app, multipart(secret), content_length=None)

    @pytest.mark.anyio
    async def test_it_uses_the_established_envelope(self, refusal: RawResponse) -> None:
        """One shape for every failure on this API."""
        assert set(refusal.payload) == {"detail"}
        assert set(refusal.payload["detail"]) == {"error", "detail"}

    @pytest.mark.anyio
    async def test_it_returns_no_part_of_the_document(self, refusal: RawResponse) -> None:
        """Not one row, not one field, not the boundary."""
        assert "merchant-secret-row" not in refusal.text
        assert "999999" not in refusal.text
        assert BOUNDARY not in refusal.text

    @pytest.mark.anyio
    async def test_it_leaks_no_internals(self, refusal: RawResponse) -> None:
        """No traceback, no path, no SQL, no parser detail."""
        text = refusal.text
        assert "Traceback" not in text
        assert "/Users" not in text
        assert "site-packages" not in text
        assert "SELECT" not in text.upper()
        assert "multipart" not in text.lower()

    @pytest.mark.anyio
    async def test_it_is_declared_as_json(self, refusal: RawResponse) -> None:
        """So a client parses it the way it parses every other error here."""
        assert refusal.headers[b"content-type"] == b"application/json"


class TestAClientThatHangsUp:
    """A disconnect mid-body is not a limit failure."""

    @pytest.mark.anyio
    async def test_a_truncated_body_does_not_become_a_413(self, limited_app: ASGIApp) -> None:
        """It is a malformed request, and it is answered as one.

        What arrived is handed over rather than held, so the parser reports a
        body it could not read instead of the limiter reporting a size problem
        that did not occur.
        """
        result = RawResponse()

        async def receive() -> Message:
            if result.delivered == 0:
                result.delivered += 1
                return {"type": "http.request", "body": b"--partial", "more_body": True}
            return {"type": "http.disconnect"}

        async def send(message: Message) -> None:
            if message["type"] == "http.response.start":
                result.status = message["status"]
            else:
                result.body += message.get("body", b"")

        scope: Scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "path": "/v1/imports",
            "raw_path": b"/v1/imports",
            "query_string": b"",
            "root_path": "",
            "scheme": "http",
            "headers": [(b"content-type", CONTENT_TYPE)],
            "client": ("127.0.0.1", 4321),
            "server": ("127.0.0.1", 8000),
        }
        await limited_app(scope, receive, send)

        assert result.status != 413
        assert result.status >= 400


class TestReplayingAnAlreadyReadBody:
    """The stream handed to the application after the body has been counted.

    The parser stops once it is told there is no more, so it never asks twice
    today. It is allowed to: `Request.is_disconnected` calls `receive` again,
    and an ASGI stream that hangs or raises on a second call would be a bug
    waiting for whichever caller does it first.
    """

    @pytest.mark.anyio
    async def test_the_body_is_delivered_once_and_completely(self) -> None:
        """One message, marked final, carrying exactly what was read."""
        receive = replaying(b"the whole body")

        assert await receive() == {
            "type": "http.request",
            "body": b"the whole body",
            "more_body": False,
        }

    @pytest.mark.anyio
    async def test_asking_again_reports_a_disconnect(self) -> None:
        """Rather than hanging, repeating the body, or raising."""
        receive = replaying(b"the whole body")
        await receive()

        assert await receive() == {"type": "http.disconnect"}
        assert await receive() == {"type": "http.disconnect"}

    @pytest.mark.anyio
    async def test_an_empty_body_is_still_delivered(self) -> None:
        """A request with no body is not the same as a disconnected one."""
        receive = replaying(b"")

        assert await receive() == {"type": "http.request", "body": b"", "more_body": False}

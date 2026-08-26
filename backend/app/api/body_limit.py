"""Bounding a request body before anything parses it.

FastAPI reads the multipart body as part of resolving an endpoint's arguments,
so any check written inside the endpoint runs after the parser has already
consumed and spooled the whole upload. A limit enforced there is not a limit on
what the server accepts; it is a limit on what the server admits to accepting.

Nor can the check live inside the stream. Raising from `receive` while the
parser is reading does not reach the caller: Starlette catches it and answers
`400 There was an error parsing the body`, which is both the wrong status and a
description of the wrong problem.

So the body is counted here, at the ASGI layer, before the application is
called at all. Every `http.request` chunk is measured as it arrives and the
count is what decides, not the `Content-Length` header, because a client that
sends no length, or a dishonest one, is exactly the client this has to stop.

The body is held in memory while it is counted, which bounds the cost of one
request at the budget plus the chunk that crossed it. That is the trade: the
parser never sees a byte until the whole body is known to fit, and in exchange
a permitted upload is buffered rather than streamed. At the sizes this budget
allows, that is the cheaper half.
"""

import json
from collections.abc import Callable

from starlette import status
from starlette.types import ASGIApp, Message, Receive, Scope, Send

AppliesTo = Callable[[Scope], bool]
"""Decides whether one request is subject to a limit."""


def post_to(path: str) -> AppliesTo:
    """Return a rule matching POST requests to one path.

    Scoped rather than global so that reading a run, listing receipts or asking
    for health keeps behaving exactly as it did. Only the route that accepts an
    upload needs its body counted, and applying this to every request would
    buffer bodies that no endpoint has any reason to bound.
    """

    def applies(scope: Scope) -> bool:
        return bool(scope.get("method") == "POST" and scope.get("path", "").rstrip("/") == path)

    return applies


class RequestBodyLimit:
    """Refuse a request body past a byte budget, before the application sees it.

    Args:
        app: The application to wrap.
        max_bytes: The largest body that may be passed on.
        applies_to: Which requests to count. Everything else is passed straight
            through, body and all.
    """

    def __init__(self, app: ASGIApp, *, max_bytes: int, applies_to: AppliesTo) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.applies_to = applies_to

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Count the body of a matching request, then pass it on or refuse it."""
        if scope["type"] != "http" or not self.applies_to(scope):
            await self.app(scope, receive, send)
            return

        if self._declares_too_much(scope):
            await self._refuse(send)
            return

        body = await self._read_within_budget(receive)
        if body is None:
            await self._refuse(send)
            return

        await self.app(scope, replaying(body), send)

    def _declares_too_much(self, scope: Scope) -> bool:
        """Return whether the client itself says the body is too big.

        An optimisation and nothing more. A client that declares an honest
        oversized length is turned away without transferring anything, which is
        worth having, but nothing depends on the header being present or true.
        The count below is the enforcement.
        """
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                declared = value.decode("latin-1")
                return declared.isdigit() and int(declared) > self.max_bytes
        return False

    async def _read_within_budget(self, receive: Receive) -> bytes | None:
        """Return the whole body, or None once it is known to be too large.

        Stops at the first chunk that carries the total past the budget, so at
        most the budget plus one chunk is ever held. The rest of the body is
        left unread: the response is a refusal, and reading megabytes in order
        to discard them would be paying the cost this exists to avoid.
        """
        chunks: list[bytes] = []
        total = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                # The client hung up. Hand over what arrived; a truncated body
                # fails in the parser, which is the honest outcome.
                return b"".join(chunks)
            chunk = message.get("body", b"")
            total += len(chunk)
            if total > self.max_bytes:
                return None
            chunks.append(chunk)
            if not message.get("more_body", False):
                return b"".join(chunks)

    async def _refuse(self, send: Send) -> None:
        """Answer 413 in the shape every other failure on this API uses.

        The wording says no import was processed, which is what matters and
        what is true. It does not claim nothing was received: by the time a
        forged or absent length is caught, some of the body has been read, and
        a message saying otherwise would be a smaller lie of the same kind this
        phase exists to remove.
        """
        payload = json.dumps(
            {
                "detail": {
                    "error": "request_too_large",
                    "detail": (
                        f"the request body is larger than the {self.max_bytes} byte "
                        "limit; no import was processed and no receipt was written"
                    ),
                }
            }
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": status.HTTP_413_CONTENT_TOO_LARGE,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(payload)).encode("latin-1")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": payload, "more_body": False})


def replaying(body: bytes) -> Receive:
    """Return a receive callable that hands over an already read body.

    Answers `http.disconnect` to any call after the first. The parser stops once
    it is told there is no more, so it does not ask twice today, but an ASGI
    stream is allowed to be asked and one that hung or raised would be a bug
    waiting for whichever caller does it first.
    """
    delivered = False

    async def receive() -> Message:
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    return receive

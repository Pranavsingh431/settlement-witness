"""Turning an HTTP upload into the two things the import service needs.

The service takes exact bytes and a label. Neither can be taken from a request
as it arrives: the bytes are unbounded until something bounds them, and the
label is a client supplied string that will be stored and shown to people.

Nothing here parses CSV or decides anything about a document. That is the
import service's work, and doing any of it twice is how two readers of the same
file start disagreeing.
"""

from fastapi import HTTPException, UploadFile, status

CHUNK_BYTES = 64 * 1024
"""How much of an upload is read at a time.

Small enough that a refused upload is abandoned early, large enough that a
normal document is a handful of reads."""

UNNAMED_DOCUMENT = "unnamed-upload.csv"
"""The document name used when a client sends none.

A constant rather than something derived from the content, because the document
name is a label for people and explicitly not an identifier. Deriving it from
the hash would make two names differ exactly when the identity differs, which
is what an identifier looks like, and someone would eventually rely on it. The
receipt already carries the document hash for that purpose."""

MAX_DOCUMENT_NAME = 200
"""Matches the stored column, so a long name is shortened here rather than
refused by the database after the import has already happened."""


def safe_document_name(raw: str | None) -> str:
    """Return a client supplied file name reduced to something safe to store.

    A file name arrives from a browser or a script and is written to the audit
    trail and read back by people, so it is treated as hostile text rather than
    as a name. Directory components are dropped, because a name like
    `../../etc/passwd` says something about the sender's intent and nothing
    about the document. Control characters are dropped, because a name carrying
    an escape sequence can rewrite a terminal that later prints the receipt.

    The result is only ever a label. The import service does not use it as an
    identifier, and neither does anything else.

    Args:
        raw: The name the client sent, if it sent one.

    Returns:
        A non-empty name of at most `MAX_DOCUMENT_NAME` characters.
    """
    if raw is None:
        return UNNAMED_DOCUMENT

    base = raw.replace("\\", "/").rsplit("/", maxsplit=1)[-1]
    cleaned = "".join(character for character in base if character.isprintable()).strip()
    if not cleaned or cleaned in {".", ".."}:
        return UNNAMED_DOCUMENT
    return cleaned[:MAX_DOCUMENT_NAME]


def read_bounded(upload: UploadFile, limit: int) -> bytes:
    """Read an upload in chunks, refusing it once it passes a limit.

    Read a piece at a time so that an oversized upload is refused after one
    chunk past the limit rather than after all of it is in memory. The bytes
    returned are exactly the bytes received: nothing is decoded, trimmed or
    normalised, because the document hash on the receipt has to describe what
    the client actually sent.

    Args:
        upload: The uploaded file.
        limit: The largest document that will be accepted, in bytes.

    Returns:
        The complete document.

    Raises:
        HTTPException: 413 when the upload is larger than the limit. Raised
            before any parsing, so an oversized document never reaches the
            service and never leaves a receipt.
    """
    chunks: list[bytes] = []
    total = 0
    while chunk := upload.file.read(CHUNK_BYTES):
        total += len(chunk)
        if total > limit:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail={
                    "error": "document_too_large",
                    "detail": (
                        f"the uploaded document is larger than the {limit} byte limit; "
                        "nothing was read and no receipt was written"
                    ),
                },
            )
        chunks.append(chunk)
    return b"".join(chunks)

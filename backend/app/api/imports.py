"""Import endpoints.

One upload path and two read paths. The upload path is a thin shell around the
Phase 2 import service: it bounds the request, names the document, and hands the
exact received bytes over. It does not read CSV, does not look at headers, and
does not decide anything about a document, because a second reader of the same
file is a second set of rules that will eventually disagree with the first.

**Every processed upload returns 201.** A rejected document is not a failed
request. It produced a receipt, that receipt is a stored resource with an
identity a caller can fetch, and the audit trail is the thing this system
exists to keep. Returning 422 for a document the parser refused would say no
resource was created when one was, and returning 200 for a duplicate would say
the same. See ADR-010.

A request that never reaches the service is a different matter. A missing field,
an unreadable enum, a record type with no parser, or an oversized body are all
refused with a 4xx and leave nothing behind.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_session, get_upload_limit
from app.api.schemas import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    ErrorEnvelope,
    ImportReceiptPage,
    ImportReceiptView,
)
from app.api.uploads import read_bounded, safe_document_name
from app.domain.facts import SourceRecordType, SourceSystem
from app.ingestion.receipts import ImportOutcome
from app.ingestion.schemas import SUPPORTED_RECORD_TYPES
from app.ingestion.service import ImportService
from app.storage.repository import ImportReceiptRepository

IMPORTS_PATH = "/v1/imports"
"""Published so the request body limiter can be scoped to this one route."""

router = APIRouter(prefix=IMPORTS_PATH, tags=["imports"])


@router.post(
    "",
    response_model=ImportReceiptView,
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {
            "model": ImportReceiptView,
            "description": (
                "A receipt was recorded. This includes a document that was rejected or "
                "was an exact duplicate: the receipt is the created resource, not the "
                "acceptance of the document. Read `outcome` to find out what happened."
            ),
        },
        413: {"model": ErrorEnvelope, "description": "The document is over the size limit"},
        422: {
            "model": ErrorEnvelope,
            "description": "The request could not be read, so nothing was imported",
        },
    },
    summary="Import one CSV document and record its receipt",
)
def create_import(
    session: Annotated[Session, Depends(get_session)],
    upload_limit: Annotated[int, Depends(get_upload_limit)],
    file: Annotated[UploadFile, File(description="The CSV document to import")],
    source_system: Annotated[
        SourceSystem,
        Form(description="Which system this document came from. Declared, never inferred."),
    ],
    record_type: Annotated[
        SourceRecordType,
        Form(description="Which schema to read the document as. Declared, never inferred."),
    ],
) -> ImportReceiptView:
    """Import one document as one record type from one source system.

    The source system and the record type are declared by the caller and are
    never taken from the file. A document read as the wrong record type fails
    loudly on its headers. A document read as the wrong source system would
    import cleanly and be wrong, which is why guessing is not offered.
    """
    if record_type not in SUPPORTED_RECORD_TYPES:
        supported = sorted(member.value for member in SUPPORTED_RECORD_TYPES)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "error": "unsupported_record_type",
                "detail": (
                    f"{record_type.value} is a source record type this contract defines "
                    f"and this parser has no CSV schema for; supported types are {supported}"
                ),
            },
        )

    content = read_bounded(file, upload_limit)
    receipt = ImportService(session).import_document(
        content,
        source_system=source_system,
        record_type=record_type,
        document_name=safe_document_name(file.filename),
    )
    return ImportReceiptView.of(receipt)


@router.get(
    "",
    response_model=ImportReceiptPage,
    summary="List import receipts, newest attempt first",
)
def list_imports(
    session: Annotated[Session, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
    outcome: Annotated[
        ImportOutcome | None, Query(description="Return only receipts with this outcome")
    ] = None,
    source_system: Annotated[
        SourceSystem | None, Query(description="Return only receipts for this declared system")
    ] = None,
    record_type: Annotated[
        SourceRecordType | None,
        Query(description="Return only receipts read as this record type"),
    ] = None,
) -> ImportReceiptPage:
    """Return a page of import receipts.

    Ordered by the database assigned sequence descending, which is the order the
    attempts were made in, reversed. That sequence is unique, so a page boundary
    lands in the same place on every call without needing a tie-breaker.

    `total` counts the receipts matching the filters rather than the whole
    history, and `filtered` says whether any filter was applied, so the two
    cannot be confused.
    """
    repository = ImportReceiptRepository(session)
    page = repository.page(
        limit=limit,
        offset=offset,
        outcome=outcome,
        source_system=source_system,
        record_type=record_type,
    )
    return ImportReceiptPage(
        receipts=[ImportReceiptView.of(receipt) for receipt in page],
        total=repository.count(
            outcome=outcome, source_system=source_system, record_type=record_type
        ),
        limit=limit,
        offset=offset,
        filtered=any(value is not None for value in (outcome, source_system, record_type)),
    )


@router.get(
    "/{receipt_id}",
    response_model=ImportReceiptView,
    responses={404: {"model": ErrorEnvelope, "description": "No such receipt"}},
    summary="Read one import receipt",
)
def get_import(
    receipt_id: str, session: Annotated[Session, Depends(get_session)]
) -> ImportReceiptView:
    """Return one receipt in full.

    The receipt is rebuilt through the model that wrote it on the way out, so a
    stored row that no longer satisfies the contract fails here rather than
    being served as though the audit trail still meant what it says.
    """
    receipt = ImportReceiptRepository(session).find(receipt_id)
    if receipt is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "not_found",
                "detail": f"no import receipt with id {receipt_id!r}",
            },
        )
    return ImportReceiptView.of(receipt)

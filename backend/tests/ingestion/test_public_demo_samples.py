"""The downloadable Track 04 files stay valid and tied to the measured corpus."""

from pathlib import Path

from sqlalchemy.orm import Session

from app.api.demo import TRACK_04_CONFIG
from app.banking.finality import BankFinalityOutcome, audit
from app.banking.snapshot import BankFinalitySnapshot
from app.benchmark.generator import generate
from app.domain.facts import SourceRecordType, SourceSystem
from app.ingestion.receipts import ImportOutcome
from app.ingestion.service import ImportService
from app.reconciliation.batch import reconcile
from app.storage.repository import SourceFactRepository
from tests.ingestion.conftest import FIXED_NOW

SAMPLE_DIR = Path(__file__).resolve().parents[3] / "frontend" / "public" / "samples"


def _import(
    session: Session,
    file_name: str,
    record_type: SourceRecordType,
    source_system: SourceSystem,
) -> None:
    """Import one downloadable sample and require whole-document acceptance."""
    receipt = ImportService(session, now=FIXED_NOW).import_document(
        (SAMPLE_DIR / file_name).read_bytes(),
        source_system=source_system,
        record_type=record_type,
        document_name=file_name,
    )
    assert receipt.outcome is ImportOutcome.ACCEPTED
    assert receipt.accepted_count == receipt.row_count


class TestPublicDemoSamples:
    """The hands-on pack is an executable extension of the one-click demo."""

    def test_provider_csvs_are_the_frozen_59_scenario_corpus(self) -> None:
        """The download links cannot drift from the batch whose metrics are shown."""
        generated = generate(TRACK_04_CONFIG)

        assert generated.manifest.scenario_count == 59
        for file_name, expected in generated.documents.items():
            assert (SAMPLE_DIR / file_name).read_text(encoding="utf-8") == expected

    def test_all_four_files_close_the_provider_and_bank_loops(self, session: Session) -> None:
        """A reviewer can import the pack, reconcile it and verify every payout credit."""
        _import(
            session,
            "payment_events.csv",
            SourceRecordType.PAYMENT_EVENT,
            SourceSystem.PSP_API,
        )
        _import(
            session,
            "settlement_lines.csv",
            SourceRecordType.SETTLEMENT_LINE,
            SourceSystem.PSP_API,
        )
        _import(session, "payouts.csv", SourceRecordType.PAYOUT, SourceSystem.PSP_API)
        _import(
            session,
            "bank_transactions.csv",
            SourceRecordType.BANK_TRANSACTION,
            SourceSystem.BANK_STATEMENT,
        )

        index = SourceFactRepository(session).fact_index()
        batch = reconcile(index)
        finality = audit(BankFinalitySnapshot.from_index(index))

        assert batch.fact_count == 236
        assert batch.settlement_line_count == 59
        assert batch.resolved_count == 32
        assert sum(batch.status_counts.values()) == 59
        assert finality.payout_count == 56
        assert finality.bank_transaction_count == 56
        assert finality.outcome_counts[BankFinalityOutcome.VERIFIED_BANK_CREDIT.value] == 56
        assert all(certificate.is_verified for certificate in finality.certificates)

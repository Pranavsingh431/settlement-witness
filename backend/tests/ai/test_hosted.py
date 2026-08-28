"""Tests for the hosted adapter. None of these touches the network.

Every request is served by an in-process `MockTransport`, so the whole file runs
with no credential, no endpoint and no connection. That is not only for speed:
a test suite that could reach a third party is a test suite that can leak, and
this is the one module in the project with anything to leak.

Three properties get the most attention, because they are what the adapter is
for. What leaves the process is presentation fields and a fixed instruction.
What comes back is judged by the existing validator and never repaired. And the
API key appears in nothing.
"""

import json
from collections.abc import Callable, Collection, Iterator, Mapping
from typing import Final

import httpx2
import pytest
from pydantic import SecretStr

from app.ai.candidates import (
    LinkProposalRequest,
    build_pages,
    build_requests,
    truth_for,
)
from app.ai.corpus import build_corpus
from app.ai.evaluation import evaluate
from app.ai.hosted import (
    ADAPTER_NAME,
    INSTRUCTION,
    TEMPERATURE,
    HostedLinkProposalProvider,
    HostedProviderConfig,
    MissingConfiguration,
    NotFingerprints,
    NothingAuthorised,
    presentation_payload,
)
from app.ai.presentation import ReferenceStyle
from app.ai.provider import FailureKind, ProviderFailure
from app.ai.validation import RejectionCode, ValidProposal, parse_proposal
from app.reconciliation.snapshot import FactSnapshot
from tests.reconciliation.conftest import index_of, payment_event, payout, settlement_line

# A fake key, so the leak checks have something to look for. It is deliberately
# low entropy and says what it is, because a realistic-looking one committed to
# a repository is a secret scanner's finding and a reviewer's wasted afternoon.
SECRET = "not-a-real-key"  # noqa: S105

ENVIRONMENT: Mapping[str, str] = {
    "SETTLEMENT_WITNESS_AI_BASE_URL": "https://api.example.test/v1",
    "SETTLEMENT_WITNESS_AI_API_KEY": SECRET,
    "SETTLEMENT_WITNESS_AI_MODEL": "some-model-1",
    "SETTLEMENT_WITNESS_AI_TIMEOUT_SECONDS": "20",
    "SETTLEMENT_WITNESS_AI_MAX_RESPONSE_BYTES": "20000",
    "SETTLEMENT_WITNESS_AI_MAX_REQUESTS": "50",
}


@pytest.fixture
def snapshot() -> FactSnapshot:
    """Return a small snapshot with one line and two candidates."""
    return FactSnapshot.from_index(
        index_of(payment_event("pe-1", payment_id="pay-1"), settlement_line("sl-1"), payout("po-1"))
    )


@pytest.fixture
def page(snapshot: FactSnapshot) -> LinkProposalRequest:
    """Return the single candidate page for that line."""
    return build_pages("line-sl-1", snapshot)[0]


@pytest.fixture
def config() -> HostedProviderConfig:
    """Return a validated configuration."""
    return HostedProviderConfig.from_environment(ENVIRONMENT)


def completion(content: str) -> httpx2.Response:
    """Return a chat-completions response carrying one assistant message."""
    return httpx2.Response(200, json={"choices": [{"message": {"content": content}}]})


def serving(
    handler: Callable[[httpx2.Request], httpx2.Response],
    config: HostedProviderConfig,
    *authorised: LinkProposalRequest,
) -> HostedLinkProposalProvider:
    """Return a provider whose requests are served in process.

    Every caller names the pages it may ask about. There is no default here
    either: a test helper that quietly authorised everything would be testing a
    provider the application never builds.
    """
    return HostedLinkProposalProvider(
        config,
        authorised_requests=frozenset(one.request_fingerprint for one in authorised),
        transport=httpx2.MockTransport(handler),
    )


def answering(payload: object) -> Callable[[httpx2.Request], httpx2.Response]:
    """Return a handler whose model always answers with one payload."""
    return lambda _request: completion(payload if isinstance(payload, str) else json.dumps(payload))


class TestConfigurationIsValidatedFirst:
    """Nothing is constructed and nothing is sent until the settings are sound."""

    @pytest.mark.parametrize("missing", sorted(ENVIRONMENT))
    def test_a_missing_variable_is_refused(self, missing: str) -> None:
        """Each one, so none of them has a quiet default."""
        environment = {key: value for key, value in ENVIRONMENT.items() if key != missing}

        with pytest.raises(MissingConfiguration, match=missing):
            HostedProviderConfig.from_environment(environment)

    def test_an_empty_variable_is_treated_as_missing(self) -> None:
        """An unset variable and a blank one are the same mistake."""
        with pytest.raises(MissingConfiguration, match="API_KEY"):
            HostedProviderConfig.from_environment(
                {**ENVIRONMENT, "SETTLEMENT_WITNESS_AI_API_KEY": ""}
            )

    def test_there_is_no_default_endpoint_model_or_key(self) -> None:
        """A misconfigured run must stop, not reach somebody's default."""
        with pytest.raises(MissingConfiguration):
            HostedProviderConfig.from_environment({})

    @pytest.mark.parametrize("field", ["TIMEOUT_SECONDS", "MAX_RESPONSE_BYTES", "MAX_REQUESTS"])
    def test_a_non_numeric_budget_is_refused(self, field: str) -> None:
        """And the message names the variable, not what was in it."""
        environment = {**ENVIRONMENT, f"SETTLEMENT_WITNESS_AI_{field}": "lots"}

        with pytest.raises(MissingConfiguration, match=field):
            HostedProviderConfig.from_environment(environment)

    @pytest.mark.parametrize(
        "budget", ["SETTLEMENT_WITNESS_AI_MAX_REQUESTS", "SETTLEMENT_WITNESS_AI_TIMEOUT_SECONDS"]
    )
    def test_a_budget_of_zero_is_refused(self, budget: str) -> None:
        """A budget nothing can satisfy is a misconfiguration, not a policy."""
        with pytest.raises(MissingConfiguration):
            HostedProviderConfig.from_environment({**ENVIRONMENT, budget: "0"})

    def test_plain_http_to_a_remote_host_is_refused(self) -> None:
        """The request carries a key in a header."""
        environment = {
            **ENVIRONMENT,
            "SETTLEMENT_WITNESS_AI_BASE_URL": "http://api.example.test/v1",
        }

        with pytest.raises(MissingConfiguration, match="https"):
            HostedProviderConfig.from_environment(environment)

    @pytest.mark.parametrize("host", ["localhost", "127.0.0.1"])
    def test_plain_http_to_the_local_machine_is_allowed(self, host: str) -> None:
        """For a fake server during development, and nothing else."""
        environment = {
            **ENVIRONMENT,
            "SETTLEMENT_WITNESS_AI_BASE_URL": f"http://{host}:9000/v1",
        }

        assert HostedProviderConfig.from_environment(environment).base_url.startswith("http://")

    @pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.test", "not-a-url"])
    def test_a_non_http_endpoint_is_refused(self, url: str) -> None:
        """Including one that names no host at all."""
        with pytest.raises(MissingConfiguration):
            HostedProviderConfig.from_environment(
                {**ENVIRONMENT, "SETTLEMENT_WITNESS_AI_BASE_URL": url}
            )


class TestTheKeyIsNeverWrittenDown:
    """Across every configuration and every failure path."""

    def test_it_is_absent_from_the_repr(self, config: HostedProviderConfig) -> None:
        """Which is where a stray print would find it."""
        assert SECRET not in repr(config)

    def test_it_is_absent_from_a_serialisation(self, config: HostedProviderConfig) -> None:
        """Which is what a receipt written by accident would contain."""
        assert SECRET not in config.model_dump_json()
        assert SECRET not in str(config.model_dump())

    def test_it_is_absent_from_the_provenance(self, config: HostedProviderConfig) -> None:
        """The provenance is what a receipt actually carries."""
        assert SECRET not in json.dumps(config.provenance())

    @pytest.mark.parametrize(
        "environment",
        [
            {**ENVIRONMENT, "SETTLEMENT_WITNESS_AI_BASE_URL": "http://remote.example.test"},
            {**ENVIRONMENT, "SETTLEMENT_WITNESS_AI_TIMEOUT_SECONDS": "nonsense"},
            {**ENVIRONMENT, "SETTLEMENT_WITNESS_AI_MAX_REQUESTS": "-1"},
            {**ENVIRONMENT, "SETTLEMENT_WITNESS_AI_MODEL": ""},
        ],
    )
    def test_it_is_absent_from_every_configuration_failure(
        self, environment: Mapping[str, str]
    ) -> None:
        """A message about a bad setting must not carry the key beside it."""
        with pytest.raises(MissingConfiguration) as raised:
            HostedProviderConfig.from_environment(environment)

        assert SECRET not in str(raised.value)
        assert SECRET not in repr(raised.value)

    def test_it_is_absent_from_a_typed_failure(
        self, config: HostedProviderConfig, page: LinkProposalRequest
    ) -> None:
        """Failures carry a kind and nothing else."""

        def refuse(_request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(401, json={"error": f"bad key {SECRET}"})

        with serving(refuse, config, page) as provider:
            result = provider.propose(page)

        assert isinstance(result, ProviderFailure)
        assert SECRET not in result.model_dump_json()

    def test_it_is_absent_from_a_run_receipt(
        self, config: HostedProviderConfig, snapshot: FactSnapshot
    ) -> None:
        """The artifact a person is most likely to paste somewhere."""
        from app.ai.evaluation import SHADOW_HARNESS_VERSION
        from app.ai.live_shadow import LIVE_RECEIPT_VERSION, LiveShadowRunReceipt

        with serving(
            answering({"outcome": "ABSTAIN", "selected_source_record_ids": []}),
            config,
            *build_requests(snapshot),
        ) as provider:
            report = evaluate(snapshot, provider)
            receipt = LiveShadowRunReceipt(
                receipt_version=LIVE_RECEIPT_VERSION,
                harness_version=SHADOW_HARNESS_VERSION,
                corpus_version="1.0.0",
                provider_name=provider.identity.name,
                model_id=config.model,
                configuration=config.provenance(),
                requests_made=provider.requests_made,
                report_rejection_counts={},
                typed_failure_counts=provider.typed_failure_counts,
                report=report,
                ran_at="2026-08-27T00:00:00+00:00",
            )

        assert SECRET not in receipt.model_dump_json()

    def test_it_is_sent_as_a_header_and_nowhere_else(
        self, config: HostedProviderConfig, page: LinkProposalRequest
    ) -> None:
        """It has to reach the host. It must not be in the body."""
        seen: dict[str, object] = {}

        def capture(request: httpx2.Request) -> httpx2.Response:
            seen["authorization"] = request.headers.get("authorization")
            seen["body"] = request.content.decode()
            return completion('{"outcome": "ABSTAIN", "selected_source_record_ids": []}')

        with serving(capture, config, page) as provider:
            provider.propose(page)

        assert seen["authorization"] == f"Bearer {SECRET}"
        assert SECRET not in str(seen["body"])


class TestOnlyPresentationLeavesTheProcess:
    """What is sent is a fixed instruction and rendered reference fields."""

    def test_the_payload_is_presentation_fields_only(self, page: LinkProposalRequest) -> None:
        """Named explicitly, so adding a field to a request cannot leak one."""
        payload = presentation_payload(page)
        subject = payload["settlement_line"]
        candidates = payload["candidates"]
        assert isinstance(subject, dict)
        assert isinstance(candidates, list)

        assert set(payload) == {"settlement_line", "page", "candidates"}
        assert set(subject) == {"id", "payment_reference", "payout_reference"}
        for candidate in candidates:
            assert set(candidate) == {
                "source_record_id",
                "record_type",
                "payment_reference",
                "payout_reference",
                "event_type",
                "occurred_at",
            }

    def test_no_fingerprint_is_sent(self, page: LinkProposalRequest) -> None:
        """Server-owned digests a model has no use for."""
        sent = json.dumps(presentation_payload(page))

        assert page.snapshot_fingerprint not in sent
        assert page.environment_fingerprint not in sent
        assert page.request_fingerprint not in sent

    def test_no_payload_hash_or_money_is_sent(
        self, page: LinkProposalRequest, snapshot: FactSnapshot
    ) -> None:
        """Canonical facts stay in the process."""
        sent = json.dumps(presentation_payload(page))

        for fact in snapshot.facts_by_record_id.values():
            assert fact.payload_hash not in sent
        for word in ("amount_minor", "net_minor", "gross_minor", "currency", "payload_hash"):
            assert word not in sent

    def test_no_private_corpus_label_is_sent(self) -> None:
        """Over the whole corpus, not one page.

        The same leak scan the corpus tests run, applied to what actually
        crosses the boundary rather than to the request object.
        """
        from app.ai.corpus import ScenarioFamily
        from app.ai.evaluation import ExpectedProviderAction

        corpus = build_corpus()
        corpus_snapshot = FactSnapshot.from_index(corpus.index)
        sent = "\n".join(
            json.dumps(presentation_payload(request))
            for request in build_requests(corpus_snapshot, corpus.styling)
        )

        for family in ScenarioFamily:
            assert family.value not in sent
        for action in ExpectedProviderAction:
            assert action.value not in sent
        for word in ("scenario", "oracle", "expected", "truth", "distractor", "manifest"):
            assert word not in sent.lower()

    def test_the_instruction_is_fixed_and_separate_from_the_data(
        self, config: HostedProviderConfig, page: LinkProposalRequest
    ) -> None:
        """Two messages, so a value in a record has no sentence to escape from.

        A structural precaution, not a solution to prompt injection, which JSON
        formatting does not solve.
        """
        seen: dict[str, object] = {}

        def capture(request: httpx2.Request) -> httpx2.Response:
            seen["body"] = json.loads(request.content)
            return completion('{"outcome": "ABSTAIN", "selected_source_record_ids": []}')

        with serving(capture, config, page) as provider:
            provider.propose(page)

        body = seen["body"]
        assert body["messages"][0] == {"role": "system", "content": INSTRUCTION}  # type: ignore[index]
        assert json.loads(body["messages"][1]["content"]) == presentation_payload(page)  # type: ignore[index]
        assert len(body["messages"]) == 2  # type: ignore[index]

    def test_it_asks_for_temperature_zero_and_a_response_schema(
        self, config: HostedProviderConfig, page: LinkProposalRequest
    ) -> None:
        """Recorded in the provenance, and requested of the host."""
        seen: dict[str, object] = {}

        def capture(request: httpx2.Request) -> httpx2.Response:
            seen["body"] = json.loads(request.content)
            return completion('{"outcome": "ABSTAIN", "selected_source_record_ids": []}')

        with serving(capture, config, page) as provider:
            provider.propose(page)

        body = seen["body"]
        assert body["temperature"] == TEMPERATURE == 0.0  # type: ignore[index]
        assert body["model"] == "some-model-1"  # type: ignore[index]
        assert body["response_format"]["type"] == "json_schema"  # type: ignore[index]

    def test_the_identity_names_the_adapter_and_the_model(
        self, config: HostedProviderConfig, page: LinkProposalRequest
    ) -> None:
        """So a report says which model produced it."""
        with serving(
            answering({"outcome": "ABSTAIN", "selected_source_record_ids": []}), config, page
        ) as provider:
            identity = provider.identity

        assert identity.name == ADAPTER_NAME
        assert identity.version == "some-model-1"


class TestAValidAnswerReachesTheOrdinaryValidator:
    """Nothing about a hosted answer is treated differently."""

    def test_a_well_formed_selection_is_returned_unchanged(
        self, config: HostedProviderConfig, page: LinkProposalRequest, snapshot: FactSnapshot
    ) -> None:
        """Straight through to the same parser a fixture's answer uses."""
        chosen = sorted(truth_for(page, snapshot))
        handler = answering({"outcome": "PROPOSE", "selected_source_record_ids": chosen})

        with serving(handler, config, page) as provider:
            raw = provider.propose(page)
            result = parse_proposal(raw, page, provider.identity)

        assert isinstance(result, ValidProposal)
        assert result.selected == set(chosen)

    def test_an_abstention_is_returned_unchanged(
        self, config: HostedProviderConfig, page: LinkProposalRequest
    ) -> None:
        """Declining is an answer here as everywhere else."""
        handler = answering({"outcome": "ABSTAIN", "selected_source_record_ids": []})

        with serving(handler, config, page) as provider:
            result = parse_proposal(provider.propose(page), page, provider.identity)

        assert isinstance(result, ValidProposal)
        assert result.abstained

    def test_the_bound_proposal_names_the_hosted_provider(
        self, config: HostedProviderConfig, page: LinkProposalRequest
    ) -> None:
        """Identity is read from the provider object, as it is for a fixture."""
        handler = answering({"outcome": "ABSTAIN", "selected_source_record_ids": []})

        with serving(handler, config, page) as provider:
            result = parse_proposal(provider.propose(page), page, provider.identity)

        assert isinstance(result, ValidProposal)
        assert result.proposal.provider.name == ADAPTER_NAME
        assert result.proposal.provider.version == "some-model-1"


class TestNothingIsRepaired:
    """Every malformed answer is refused as it arrived."""

    @pytest.mark.parametrize(
        ("label", "content"),
        [
            (
                "markdown fenced",
                '```json\n{"outcome": "ABSTAIN", "selected_source_record_ids": []}\n```',
            ),
            (
                "prose wrapped",
                'Sure! Here you go: {"outcome": "ABSTAIN", "selected_source_record_ids": []}',
            ),
            ("not json", "I think none of these belong."),
            ("empty string", ""),
            ("json array", "[]"),
        ],
    )
    def test_a_response_that_is_not_the_shape_is_a_typed_failure(
        self, config: HostedProviderConfig, page: LinkProposalRequest, label: str, content: str
    ) -> None:
        """Fences are not stripped and prose is not searched for an object.

        Doing either would be the adapter deciding what the model meant, and a
        report over repaired answers cannot say which part was whose.
        """
        with serving(answering(content), config, page) as provider:
            result = provider.propose(page)

        if isinstance(result, ProviderFailure):
            assert result.kind is FailureKind.UNREADABLE_RESPONSE
        else:
            assert isinstance(
                parse_proposal(result, page, provider.identity).code,  # type: ignore[union-attr]
                RejectionCode,
            )

    @pytest.mark.parametrize(
        ("label", "payload"),
        [
            ("extra field", {"outcome": "ABSTAIN", "selected_source_record_ids": [], "why": "x"}),
            (
                "confidence",
                {"outcome": "ABSTAIN", "selected_source_record_ids": [], "confidence": 0.9},
            ),
            (
                "status",
                {"outcome": "PROPOSE", "selected_source_record_ids": ["a"], "status": "RESOLVED"},
            ),
            (
                "provider identity",
                {"outcome": "ABSTAIN", "selected_source_record_ids": [], "provider": {}},
            ),
            (
                "page metadata",
                {"outcome": "ABSTAIN", "selected_source_record_ids": [], "page_ordinal": 1},
            ),
            (
                "subject",
                {
                    "outcome": "ABSTAIN",
                    "selected_source_record_ids": [],
                    "subject_settlement_line_id": "x",
                },
            ),
            (
                "snapshot",
                {
                    "outcome": "ABSTAIN",
                    "selected_source_record_ids": [],
                    "snapshot_fingerprint": "f" * 64,
                },
            ),
            ("missing outcome", {"selected_source_record_ids": []}),
            ("bad outcome", {"outcome": "MAYBE", "selected_source_record_ids": []}),
            (
                "abstention with records",
                {"outcome": "ABSTAIN", "selected_source_record_ids": ["a"]},
            ),
            ("proposal with none", {"outcome": "PROPOSE", "selected_source_record_ids": []}),
        ],
    )
    def test_a_forbidden_or_contradictory_answer_is_rejected(
        self,
        config: HostedProviderConfig,
        page: LinkProposalRequest,
        label: str,
        payload: Mapping[str, object],
    ) -> None:
        """The same rules a fixture's answer meets, not a looser set."""
        with serving(answering(payload), config, page) as provider:
            result = parse_proposal(provider.propose(page), page, provider.identity)

        assert result.code is RejectionCode.MALFORMED  # type: ignore[union-attr]

    def test_an_unknown_record_id_is_rejected(
        self, config: HostedProviderConfig, page: LinkProposalRequest
    ) -> None:
        """A model cannot name a record it was not offered."""
        handler = answering(
            {"outcome": "PROPOSE", "selected_source_record_ids": ["PAYMENT_EVENT:invented"]}
        )

        with serving(handler, config, page) as provider:
            result = parse_proposal(provider.propose(page), page, provider.identity)

        assert result.code is RejectionCode.OUT_OF_CANDIDATE_SET  # type: ignore[union-attr]

    def test_a_duplicate_id_is_rejected(
        self, config: HostedProviderConfig, page: LinkProposalRequest
    ) -> None:
        """Not deduplicated on the way through."""
        chosen = sorted(page.candidate_ids)[0]
        handler = answering({"outcome": "PROPOSE", "selected_source_record_ids": [chosen, chosen]})

        with serving(handler, config, page) as provider:
            result = parse_proposal(provider.propose(page), page, provider.identity)

        assert result.code is RejectionCode.MALFORMED  # type: ignore[union-attr]

    def test_an_oversized_selection_is_rejected(
        self, config: HostedProviderConfig, page: LinkProposalRequest
    ) -> None:
        """Not truncated to the limit."""
        handler = answering(
            {
                "outcome": "PROPOSE",
                "selected_source_record_ids": [f"rec-{index}" for index in range(200)],
            }
        )

        with serving(handler, config, page) as provider:
            result = parse_proposal(provider.propose(page), page, provider.identity)

        assert result.code is RejectionCode.MALFORMED  # type: ignore[union-attr]

    def test_a_record_from_another_page_is_rejected(self, config: HostedProviderConfig) -> None:
        """Membership is against the page, for a hosted model too."""
        facts = [
            payment_event(f"pe-{index:03d}", payment_id="pay-1", amount_minor=1000 + index)
            for index in range(70)
        ]
        facts += [settlement_line("sl-1", payment_id="pay-1"), payout("po-1")]
        wide = FactSnapshot.from_index(index_of(*facts))
        pages = build_pages("line-sl-1", wide)
        from_page_two = sorted(pages[1].candidate_ids)[0]
        handler = answering({"outcome": "PROPOSE", "selected_source_record_ids": [from_page_two]})

        with serving(handler, config, pages[0]) as provider:
            result = parse_proposal(provider.propose(pages[0]), pages[0], provider.identity)

        assert result.code is RejectionCode.OUT_OF_CANDIDATE_SET  # type: ignore[union-attr]


class TestFailuresAreTypedAndCarryNothing:
    """A host that did not answer, in each of the ways it can fail."""

    def test_a_timeout(self, config: HostedProviderConfig, page: LinkProposalRequest) -> None:
        """The configured timeout, not a retry loop."""

        def slow(_request: httpx2.Request) -> httpx2.Response:
            raise httpx2.TimeoutException("too slow")

        with serving(slow, config, page) as provider:
            result = provider.propose(page)

        assert isinstance(result, ProviderFailure)
        assert result.kind is FailureKind.TIMED_OUT

    def test_a_connection_failure(
        self, config: HostedProviderConfig, page: LinkProposalRequest
    ) -> None:
        """The endpoint could not be reached."""

        def unreachable(_request: httpx2.Request) -> httpx2.Response:
            raise httpx2.ConnectError("no route")

        with serving(unreachable, config, page) as provider:
            result = provider.propose(page)

        assert isinstance(result, ProviderFailure)
        assert result.kind is FailureKind.CONNECTION_FAILED

    @pytest.mark.parametrize("status", [400, 401, 403, 429, 500, 502, 503])
    def test_a_non_success_status(
        self, config: HostedProviderConfig, page: LinkProposalRequest, status: int
    ) -> None:
        """A rate limit and an authentication failure are the same fact here."""
        handler = lambda _request: httpx2.Response(  # noqa: E731
            status, json={"error": {"message": "provider prose that must not be kept"}}
        )

        with serving(handler, config, page) as provider:
            result = provider.propose(page)

        assert isinstance(result, ProviderFailure)
        assert result.kind is FailureKind.REFUSED_BY_PROVIDER
        assert "prose" not in result.model_dump_json()

    def test_an_oversized_body(
        self, config: HostedProviderConfig, page: LinkProposalRequest
    ) -> None:
        """Abandoned rather than parsed."""
        huge = "x" * (config.max_response_bytes + 1)
        handler = lambda _request: httpx2.Response(200, content=huge.encode())  # noqa: E731

        with serving(handler, config, page) as provider:
            result = provider.propose(page)

        assert isinstance(result, ProviderFailure)
        assert result.kind is FailureKind.RESPONSE_TOO_LARGE

    def test_an_empty_body(self, config: HostedProviderConfig, page: LinkProposalRequest) -> None:
        """A host that answered with nothing did not answer."""
        handler = lambda _request: httpx2.Response(200, content=b"")  # noqa: E731

        with serving(handler, config, page) as provider:
            result = provider.propose(page)

        assert isinstance(result, ProviderFailure)
        assert result.kind is FailureKind.RETURNED_NOTHING

    @pytest.mark.parametrize(
        "envelope",
        [
            {"choices": []},
            {"choices": "not a list"},
            {"choices": [{"message": {}}]},
            {"choices": [{"message": {"content": 42}}]},
            {"choices": ["not an object"]},
            {"choices": [{"no_message": True}]},
            {"unexpected": "shape"},
            ["not an object at all"],
        ],
    )
    def test_a_malformed_provider_envelope(
        self, config: HostedProviderConfig, page: LinkProposalRequest, envelope: object
    ) -> None:
        """The envelope being wrong, as against the answer inside it.

        Refused rather than searched: hunting a response for something that
        looks like an answer is how an adapter starts inventing one.
        """
        handler = lambda _request: httpx2.Response(200, json=envelope)  # noqa: E731

        with serving(handler, config, page) as provider:
            result = provider.propose(page)

        assert isinstance(result, ProviderFailure)
        assert result.kind is FailureKind.UNREADABLE_RESPONSE

    def test_a_body_that_is_not_json(
        self, config: HostedProviderConfig, page: LinkProposalRequest
    ) -> None:
        """An HTML error page, for instance."""
        handler = lambda _request: httpx2.Response(200, content=b"<html>502</html>")  # noqa: E731

        with serving(handler, config, page) as provider:
            result = provider.propose(page)

        assert isinstance(result, ProviderFailure)
        assert result.kind is FailureKind.UNREADABLE_RESPONSE

    def test_no_failure_carries_provider_text(
        self, config: HostedProviderConfig, page: LinkProposalRequest
    ) -> None:
        """A failure has a kind and nothing else. Asserted over the model."""
        from app.ai.provider import ProviderFailure as Failure

        assert set(Failure.model_fields) == {"kind"}


class TestNothingIsRetried:
    """One request per page, whatever came back."""

    @pytest.mark.parametrize(
        "handler_name", ["timeout", "connection", "rate limited", "server error", "unreadable"]
    )
    def test_a_failure_sends_exactly_one_request(
        self, config: HostedProviderConfig, page: LinkProposalRequest, handler_name: str
    ) -> None:
        """A retry would make the request count a lie and could double a charge."""
        attempts = {"count": 0}

        def handler(_request: httpx2.Request) -> httpx2.Response:
            attempts["count"] += 1
            if handler_name == "timeout":
                raise httpx2.TimeoutException("slow")
            if handler_name == "connection":
                raise httpx2.ConnectError("no route")
            if handler_name == "rate limited":
                return httpx2.Response(429, json={})
            if handler_name == "server error":
                return httpx2.Response(500, json={})
            return httpx2.Response(200, content=b"not json")

        with serving(handler, config, page) as provider:
            provider.propose(page)

        assert attempts["count"] == 1
        assert provider.requests_made == 1

    def test_a_rejected_answer_sends_exactly_one_request(
        self, config: HostedProviderConfig, page: LinkProposalRequest
    ) -> None:
        """A bad answer is recorded, not asked for again."""
        attempts = {"count": 0}

        def handler(_request: httpx2.Request) -> httpx2.Response:
            attempts["count"] += 1
            return completion('{"outcome": "PROPOSE", "selected_source_record_ids": ["nope"]}')

        with serving(handler, config, page) as provider:
            provider.propose(page)

        assert attempts["count"] == 1


class TestTheRequestBudgetStops:
    """A run cannot exceed the number of requests it was allowed."""

    def test_it_stops_before_the_maximum_is_passed(self, page: LinkProposalRequest) -> None:
        """The budget is a limit on requests sent, not on requests attempted."""
        config = HostedProviderConfig.from_environment(
            {**ENVIRONMENT, "SETTLEMENT_WITNESS_AI_MAX_REQUESTS": "2"}
        )
        attempts = {"count": 0}

        def handler(_request: httpx2.Request) -> httpx2.Response:
            attempts["count"] += 1
            return completion('{"outcome": "ABSTAIN", "selected_source_record_ids": []}')

        with serving(handler, config, page) as provider:
            first = provider.propose(page)
            second = provider.propose(page)
            third = provider.propose(page)

        assert attempts["count"] == 2
        assert not isinstance(first, ProviderFailure)
        assert not isinstance(second, ProviderFailure)
        assert isinstance(third, ProviderFailure)
        assert third.kind is FailureKind.BUDGET_EXHAUSTED

    def test_an_exhausted_budget_is_a_typed_failure_not_a_selection(
        self, page: LinkProposalRequest
    ) -> None:
        """Nothing is invented to stand in for the answer that was not asked for."""
        config = HostedProviderConfig.from_environment(
            {**ENVIRONMENT, "SETTLEMENT_WITNESS_AI_MAX_REQUESTS": "1"}
        )
        handler = answering({"outcome": "ABSTAIN", "selected_source_record_ids": []})

        with serving(handler, config, page) as provider:
            provider.propose(page)
            exhausted = provider.propose(page)

        assert isinstance(exhausted, ProviderFailure)
        assert provider.requests_made == 1

    def test_a_whole_corpus_run_respects_the_budget(self) -> None:
        """The case the budget exists for.

        The corpus is 24 pages. A budget of five means five requests and
        nineteen pages recorded as unanswered, rather than a run that quietly
        costs five times what was authorised.
        """
        config = HostedProviderConfig.from_environment(
            {**ENVIRONMENT, "SETTLEMENT_WITNESS_AI_MAX_REQUESTS": "5"}
        )
        corpus = build_corpus()
        corpus_snapshot = FactSnapshot.from_index(corpus.index)
        attempts = {"count": 0}

        def handler(_request: httpx2.Request) -> httpx2.Response:
            attempts["count"] += 1
            return completion('{"outcome": "ABSTAIN", "selected_source_record_ids": []}')

        with serving(handler, config, *build_requests(corpus_snapshot, corpus.styling)) as provider:
            report = evaluate(corpus_snapshot, provider, corpus.expected_actions, corpus.styling)

        assert attempts["count"] == 5
        assert provider.requests_made == 5
        assert report.page_count == 24
        assert report.invalid_page_rate.numerator == 19


class TestTheRemainingEdges:
    """Two branches that only fire on unusual input."""

    def test_a_url_with_no_host_is_refused(self) -> None:
        """`https:///v1` parses as a URL and names nowhere to send a key."""
        with pytest.raises(MissingConfiguration, match="names no host"):
            HostedProviderConfig.from_environment(
                {**ENVIRONMENT, "SETTLEMENT_WITNESS_AI_BASE_URL": "https:///v1"}
            )

    def test_an_unexpected_http_error_is_a_typed_failure(
        self, config: HostedProviderConfig, page: LinkProposalRequest
    ) -> None:
        """The catch-all, so nothing from the client escapes a run.

        A run over a corpus records what went wrong on a page and carries on. An
        exception escaping here would end the run at whichever page failed
        first, and the pages after it would be missing rather than reported.
        """

        def odd(_request: httpx2.Request) -> httpx2.Response:
            raise httpx2.TooManyRedirects("looping")

        with serving(odd, config, page) as provider:
            result = provider.propose(page)

        assert isinstance(result, ProviderFailure)
        assert result.kind is FailureKind.RAISED


class RecordingStream(httpx2.SyncByteStream):
    """A chunked response body that counts every byte it hands out.

    The whole point of these tests is what the adapter did **not** read, and a
    plain `httpx2.Response(200, content=...)` cannot answer that: it is already
    in memory before the adapter sees it. This yields the body a chunk at a time
    and records how much was actually taken, so an assertion can be about the
    read rather than about the outcome the read happened to produce.
    """

    def __init__(self, payload: bytes, chunk: int = 4096) -> None:
        self._payload = payload
        self._chunk = chunk
        self.consumed = 0

    @property
    def chunk_size(self) -> int:
        """Return the size of one chunk, for a budget-plus-one assertion."""
        return self._chunk

    def __iter__(self) -> Iterator[bytes]:
        """Yield the body in fixed size pieces, counting as it goes."""
        for start in range(0, len(self._payload), self._chunk):
            piece = self._payload[start : start + self._chunk]
            self.consumed += len(piece)
            yield piece


ABSTAIN: Final = '{"outcome": "ABSTAIN", "selected_source_record_ids": []}'
"""A valid answer, so an oversized envelope is oversized and nothing else."""


def padded(size: int) -> bytes:
    """Return a valid completion envelope padded past any sane budget."""
    return json.dumps(
        {"choices": [{"message": {"content": ABSTAIN}}], "padding": "p" * size}
    ).encode("utf-8")


def streaming(
    stream: RecordingStream, status: int = 200, headers: Mapping[str, str] | None = None
) -> Callable[[httpx2.Request], httpx2.Response]:
    """Return a handler that answers with one recorded stream."""
    return lambda _request: httpx2.Response(status, stream=stream, headers=dict(headers or {}))


class TestTheResponseIsReadUnderBudget:
    """An oversized answer costs a bounded read, not a full download.

    Reading the whole body and then measuring it is not a byte budget. It is a
    report of how much was already spent, and a host that answers with a
    gigabyte would be paid in full before being refused.
    """

    @pytest.fixture
    def config(self) -> HostedProviderConfig:
        """Return a configuration with a small, easily crossed budget."""
        return HostedProviderConfig.from_environment(
            {**ENVIRONMENT, "SETTLEMENT_WITNESS_AI_MAX_RESPONSE_BYTES": "1000"}
        )

    def test_an_oversized_body_with_no_length_stops_while_streaming(
        self, config: HostedProviderConfig, page: LinkProposalRequest
    ) -> None:
        """No Content-Length at all, which is what a chunked host sends."""
        stream = RecordingStream(padded(200_000))

        with serving(streaming(stream), config, page) as provider:
            result = provider.propose(page)

        assert isinstance(result, ProviderFailure)
        assert result.kind is FailureKind.RESPONSE_TOO_LARGE
        assert stream.consumed <= config.max_response_bytes + stream.chunk_size
        assert stream.consumed < 200_000

    def test_a_forged_small_length_buys_no_extra_reading(
        self, config: HostedProviderConfig, page: LinkProposalRequest
    ) -> None:
        """A host that declares 40 bytes and sends 200 kilobytes.

        The declared size is a convenience for the honest case and is never the
        thing relied on. A lie about it changes nothing.
        """
        stream = RecordingStream(padded(200_000))

        with serving(streaming(stream, headers={"content-length": "40"}), config, page) as provider:
            result = provider.propose(page)

        assert isinstance(result, ProviderFailure)
        assert result.kind is FailureKind.RESPONSE_TOO_LARGE
        assert stream.consumed <= config.max_response_bytes + stream.chunk_size
        assert stream.consumed < 200_000

    def test_an_honest_oversized_length_is_never_read_at_all(
        self, config: HostedProviderConfig, page: LinkProposalRequest
    ) -> None:
        """The cheap case: refused before a single byte is asked for."""
        body = padded(200_000)
        stream = RecordingStream(body)

        with serving(
            streaming(stream, headers={"content-length": str(len(body))}), config, page
        ) as provider:
            result = provider.propose(page)

        assert isinstance(result, ProviderFailure)
        assert result.kind is FailureKind.RESPONSE_TOO_LARGE
        assert stream.consumed == 0

    @pytest.mark.parametrize("status", [401, 429, 500, 503])
    def test_a_refused_response_body_is_never_consumed(
        self, config: HostedProviderConfig, page: LinkProposalRequest, status: int
    ) -> None:
        """A host that refuses can also be a host that explains at length.

        None of that explanation is wanted, so none of it is read.
        """
        stream = RecordingStream(b"why this failed, at length. " * 20_000)

        with serving(streaming(stream, status=status), config, page) as provider:
            result = provider.propose(page)

        assert isinstance(result, ProviderFailure)
        assert result.kind is FailureKind.REFUSED_BY_PROVIDER
        assert stream.consumed == 0

    def test_a_body_inside_the_budget_is_read_and_answered(
        self, config: HostedProviderConfig, page: LinkProposalRequest
    ) -> None:
        """The ordinary case still works, chunked and all."""
        body = json.dumps({"choices": [{"message": {"content": ABSTAIN}}]}).encode("utf-8")
        stream = RecordingStream(body, chunk=16)

        with serving(streaming(stream), config, page) as provider:
            result = provider.propose(page)

        assert result == {"outcome": "ABSTAIN", "selected_source_record_ids": []}
        assert stream.consumed == len(body)

    def test_a_body_exactly_at_the_budget_is_accepted(
        self, config: HostedProviderConfig, page: LinkProposalRequest
    ) -> None:
        """The budget is a limit, not a limit minus one."""
        content = json.dumps({"choices": [{"message": {"content": ABSTAIN}}], "pad": ""})
        body = content.replace('"pad": ""', '"pad": "' + "p" * (1000 - len(content)) + '"')

        assert len(body.encode("utf-8")) == config.max_response_bytes

        stream = RecordingStream(body.encode("utf-8"), chunk=64)
        with serving(streaming(stream), config, page) as provider:
            result = provider.propose(page)

        assert result == {"outcome": "ABSTAIN", "selected_source_record_ids": []}

    def test_one_byte_over_the_budget_is_refused(
        self, config: HostedProviderConfig, page: LinkProposalRequest
    ) -> None:
        """Read one byte at a time, so the stopping point is exact."""
        stream = RecordingStream(b"x" * (config.max_response_bytes + 1), chunk=1)

        with serving(streaming(stream), config, page) as provider:
            result = provider.propose(page)

        assert isinstance(result, ProviderFailure)
        assert result.kind is FailureKind.RESPONSE_TOO_LARGE
        assert stream.consumed == config.max_response_bytes + 1

    def test_a_nonsense_content_length_is_ignored_and_streaming_decides(
        self, config: HostedProviderConfig, page: LinkProposalRequest
    ) -> None:
        """A header that is not a number is not trusted and not fatal."""
        stream = RecordingStream(padded(200_000))

        with serving(
            streaming(stream, headers={"content-length": "not-a-number"}), config, page
        ) as provider:
            result = provider.propose(page)

        assert isinstance(result, ProviderFailure)
        assert result.kind is FailureKind.RESPONSE_TOO_LARGE
        assert stream.consumed <= config.max_response_bytes + stream.chunk_size


class TestOnlyAuthorisedPagesAreAsked:
    """Corpus-only is a property of the adapter, not of the command's options.

    A command with no `--database` flag is corpus-only until somebody writes a
    second caller. An adapter that refuses every page it was not given is
    corpus-only whoever calls it.
    """

    @pytest.fixture
    def corpus_pages(self) -> tuple[LinkProposalRequest, ...]:
        """Return every page of the shadow corpus, canonically styled."""
        corpus = build_corpus()
        return build_requests(FactSnapshot.from_index(corpus.index), corpus.styling)

    @pytest.fixture
    def authorised(self, corpus_pages: tuple[LinkProposalRequest, ...]) -> frozenset[str]:
        """Return the allow-list the command would build."""
        return frozenset(one.request_fingerprint for one in corpus_pages)

    @staticmethod
    def _counting() -> tuple[list[str], Callable[[httpx2.Request], httpx2.Response]]:
        """Return a call log and a handler that appends to it."""
        seen: list[str] = []

        def handler(request: httpx2.Request) -> httpx2.Response:
            seen.append(str(request.url))
            return completion(ABSTAIN)

        return seen, handler

    def test_a_page_from_another_snapshot_is_refused_before_any_request(
        self, config: HostedProviderConfig, authorised: frozenset[str], page: LinkProposalRequest
    ) -> None:
        """The defect this class exists for: a real snapshot reached the wire."""
        seen, handler = self._counting()

        with HostedLinkProposalProvider(
            config, authorised_requests=authorised, transport=httpx2.MockTransport(handler)
        ) as provider:
            result = provider.propose(page)

        assert isinstance(result, ProviderFailure)
        assert result.kind is FailureKind.REQUEST_NOT_AUTHORIZED
        assert seen == []

    def test_the_same_corpus_under_different_styling_is_refused(
        self, config: HostedProviderConfig, authorised: frozenset[str]
    ) -> None:
        """Same records, different question.

        The allow-list is over request fingerprints, which cover what a provider
        was shown and not only which records existed. Re-rendering the corpus is
        a different set of questions and is not authorised by the first set.
        """
        corpus = build_corpus()
        snapshot = FactSnapshot.from_index(corpus.index)
        restyled = build_requests(snapshot, dict.fromkeys(corpus.styling, ReferenceStyle.SPACED))
        seen, handler = self._counting()

        assert {one.request_fingerprint for one in restyled}.isdisjoint(authorised)

        with HostedLinkProposalProvider(
            config, authorised_requests=authorised, transport=httpx2.MockTransport(handler)
        ) as provider:
            results = [provider.propose(one) for one in restyled]

        assert seen == []
        assert all(
            isinstance(one, ProviderFailure) and one.kind is FailureKind.REQUEST_NOT_AUTHORIZED
            for one in results
        )

    def test_every_authorised_corpus_page_reaches_the_host(
        self,
        config: HostedProviderConfig,
        authorised: frozenset[str],
        corpus_pages: tuple[LinkProposalRequest, ...],
    ) -> None:
        """The allow-list refuses what it should and nothing else."""
        seen, handler = self._counting()

        with HostedLinkProposalProvider(
            config, authorised_requests=authorised, transport=httpx2.MockTransport(handler)
        ) as provider:
            results = [provider.propose(one) for one in corpus_pages]

        assert len(seen) == len(corpus_pages) == 24
        assert all(
            one == {"outcome": "ABSTAIN", "selected_source_record_ids": []} for one in results
        )

    def test_an_unauthorised_page_costs_no_budget(
        self, config: HostedProviderConfig, authorised: frozenset[str], page: LinkProposalRequest
    ) -> None:
        """Nothing was sent, so nothing was spent."""
        seen, handler = self._counting()

        with HostedLinkProposalProvider(
            config, authorised_requests=authorised, transport=httpx2.MockTransport(handler)
        ) as provider:
            provider.propose(page)
            provider.propose(page)

            assert provider.requests_made == 0

        assert seen == []

    def test_it_is_refused_even_when_the_budget_is_gone(
        self, authorised: frozenset[str], page: LinkProposalRequest
    ) -> None:
        """Authorisation is checked first, so the answer never depends on it.

        Whether an unauthorised page would have fitted the budget is not a
        question worth asking, and a run that reported BUDGET_EXHAUSTED for a
        page it was never allowed to ask would be hiding the real problem.
        """
        config = HostedProviderConfig.from_environment(
            {**ENVIRONMENT, "SETTLEMENT_WITNESS_AI_MAX_REQUESTS": "1"}
        )
        seen, handler = self._counting()

        with HostedLinkProposalProvider(
            config, authorised_requests=authorised, transport=httpx2.MockTransport(handler)
        ) as provider:
            result = provider.propose(page)

        assert isinstance(result, ProviderFailure)
        assert result.kind is FailureKind.REQUEST_NOT_AUTHORIZED
        assert seen == []

    def test_it_cannot_be_built_without_an_allow_list_at_all(
        self, config: HostedProviderConfig
    ) -> None:
        """Keyword only and required, so it cannot be forgotten quietly."""
        with pytest.raises(TypeError):
            HostedLinkProposalProvider(config)  # type: ignore[call-arg]

    def test_a_caller_cannot_widen_the_scope_through_a_mutable_set(
        self,
        config: HostedProviderConfig,
        corpus_pages: tuple[LinkProposalRequest, ...],
    ) -> None:
        """The one that matters: a plain `set`, widened after construction.

        An annotation saying `frozenset[str]` is not a runtime contract. Python
        does not check it, so a caller passing an ordinary set kept a live
        handle on the provider's own scope and could add to it at any point in
        a run. The provider takes its own copy, so there is nothing to widen.
        """
        first, second = corpus_pages[0], corpus_pages[1]
        mutable = {first.request_fingerprint}
        seen, handler = self._counting()

        with HostedLinkProposalProvider(
            config, authorised_requests=mutable, transport=httpx2.MockTransport(handler)
        ) as provider:
            mutable.add(second.request_fingerprint)
            result = provider.propose(second)

            assert provider.requests_made == 0

        assert isinstance(result, ProviderFailure)
        assert result.kind is FailureKind.REQUEST_NOT_AUTHORIZED
        assert seen == []

    def test_the_page_it_was_built_with_still_works_afterwards(
        self,
        config: HostedProviderConfig,
        corpus_pages: tuple[LinkProposalRequest, ...],
    ) -> None:
        """The copy is a copy of what was passed, not a refusal of everything."""
        first, second = corpus_pages[0], corpus_pages[1]
        mutable = {first.request_fingerprint}
        seen, handler = self._counting()

        with HostedLinkProposalProvider(
            config, authorised_requests=mutable, transport=httpx2.MockTransport(handler)
        ) as provider:
            mutable.add(second.request_fingerprint)
            mutable.discard(first.request_fingerprint)
            result = provider.propose(first)

        assert result == {"outcome": "ABSTAIN", "selected_source_record_ids": []}
        assert len(seen) == 1

    def test_a_list_is_snapshotted_too(
        self,
        config: HostedProviderConfig,
        corpus_pages: tuple[LinkProposalRequest, ...],
    ) -> None:
        """Any collection is accepted and none of them is retained."""
        first, second = corpus_pages[0], corpus_pages[1]
        supplied = [first.request_fingerprint]
        seen, handler = self._counting()

        with HostedLinkProposalProvider(
            config, authorised_requests=supplied, transport=httpx2.MockTransport(handler)
        ) as provider:
            supplied.append(second.request_fingerprint)
            result = provider.propose(second)

        assert isinstance(result, ProviderFailure)
        assert result.kind is FailureKind.REQUEST_NOT_AUTHORIZED
        assert seen == []

    def test_a_frozenset_authorises_every_canonical_corpus_page(
        self,
        config: HostedProviderConfig,
        corpus_pages: tuple[LinkProposalRequest, ...],
    ) -> None:
        """The ordinary case, passed the way the command passes it."""
        seen, handler = self._counting()
        supplied = frozenset(one.request_fingerprint for one in corpus_pages)

        with HostedLinkProposalProvider(
            config, authorised_requests=supplied, transport=httpx2.MockTransport(handler)
        ) as provider:
            results = [provider.propose(one) for one in corpus_pages]

            assert provider.requests_made == 24

        assert len(seen) == 24
        assert all(
            one == {"outcome": "ABSTAIN", "selected_source_record_ids": []} for one in results
        )

    @pytest.mark.parametrize("empty", [frozenset(), set(), [], (), {}])
    def test_an_empty_collection_of_any_kind_is_refused(
        self, config: HostedProviderConfig, empty: Collection[str]
    ) -> None:
        """The no-permissive-default rule, whatever shape the emptiness arrives in."""
        with pytest.raises(NothingAuthorised) as raised:
            HostedLinkProposalProvider(config, authorised_requests=empty)

        assert "permissive default" in str(raised.value)

    def test_a_bare_string_is_refused_rather_than_split_into_letters(
        self, config: HostedProviderConfig, corpus_pages: tuple[LinkProposalRequest, ...]
    ) -> None:
        """A string is a collection of characters, which is the trap.

        Snapshotting one would build an allow-list of single letters: immutable,
        non-empty, and refusing every real page. The provider would look built
        and every page of the run would come back unauthorised.
        """
        with pytest.raises(NotFingerprints) as raised:
            HostedLinkProposalProvider(
                config, authorised_requests=corpus_pages[0].request_fingerprint
            )

        assert "not a single string" in str(raised.value)

    def test_bytes_are_refused_for_the_same_reason(self, config: HostedProviderConfig) -> None:
        """The same trap, one type along.

        mypy catches this one and does not catch the `str` above, because a
        `str` really is a `Collection[str]`. Which is the argument for the
        runtime guard: the type checker agrees with the annotation and the
        annotation is not the guarantee.
        """
        with pytest.raises(NotFingerprints):
            HostedLinkProposalProvider(
                config,
                authorised_requests=b"a" * 64,  # type: ignore[arg-type]
            )

    def test_a_member_that_is_not_a_fingerprint_is_refused(
        self, config: HostedProviderConfig
    ) -> None:
        """Silently never matching would look like a model that refused everything."""
        with pytest.raises(NotFingerprints) as raised:
            HostedLinkProposalProvider(
                config,
                authorised_requests=frozenset({"a" * 64, 7}),  # type: ignore[arg-type]
            )

        assert "fingerprint string" in str(raised.value)


class TestTheAdapterCountsItsOwnFailures:
    """The adapter knows why a page failed. The shared report does not.

    `ShadowReport` says `PROVIDER_FAILED` for every provider that did not
    answer, which is right for a report that also scores fixtures and useless
    for working out why a hosted run went badly. Both are kept, separately.
    """

    @pytest.fixture
    def small(self) -> HostedProviderConfig:
        """Return a configuration with a small response budget."""
        return HostedProviderConfig.from_environment(
            {**ENVIRONMENT, "SETTLEMENT_WITNESS_AI_MAX_RESPONSE_BYTES": "500"}
        )

    def test_it_starts_empty(self, config: HostedProviderConfig, page: LinkProposalRequest) -> None:
        """Rather than reporting zeroes for kinds that have not occurred."""
        with serving(answering(ABSTAIN), config, page) as provider:
            assert provider.typed_failure_counts == {}

    def test_an_answered_page_counts_nothing(
        self, config: HostedProviderConfig, page: LinkProposalRequest
    ) -> None:
        """Only failures are counted, so a count is a count of problems."""
        with serving(answering(ABSTAIN), config, page) as provider:
            provider.propose(page)

            assert provider.typed_failure_counts == {}

    def test_a_rejected_answer_is_not_a_provider_failure(
        self, config: HostedProviderConfig, page: LinkProposalRequest
    ) -> None:
        """A model that answered badly is not a provider that failed.

        The envelope was fine and the answer arrived. What is wrong with it is
        the validator's business, and counting it here would put a model's
        mistake in the column that says the host was unreachable.
        """
        with serving(answering('{"outcome": "NONSENSE"}'), config, page) as provider:
            provider.propose(page)

            assert provider.typed_failure_counts == {}

    def test_a_timeout_is_counted_as_one(
        self, config: HostedProviderConfig, page: LinkProposalRequest
    ) -> None:
        """One test per kind, so no kind is silently uncounted."""

        def slow(_request: httpx2.Request) -> httpx2.Response:
            raise httpx2.ReadTimeout("too slow")

        with serving(slow, config, page) as provider:
            provider.propose(page)

            assert provider.typed_failure_counts == {"TIMED_OUT": 1}

    def test_a_connection_failure_is_counted_as_one(
        self, config: HostedProviderConfig, page: LinkProposalRequest
    ) -> None:
        """A dead socket, which must never read as a rate limit."""

        def unreachable(_request: httpx2.Request) -> httpx2.Response:
            raise httpx2.ConnectError("no route to host")

        with serving(unreachable, config, page) as provider:
            provider.propose(page)

            assert provider.typed_failure_counts == {"CONNECTION_FAILED": 1}

    def test_an_unexpected_http_error_is_counted_as_one(
        self, config: HostedProviderConfig, page: LinkProposalRequest
    ) -> None:
        """Something httpx raised that is neither a timeout nor a transport error."""

        def odd(_request: httpx2.Request) -> httpx2.Response:
            raise httpx2.TooManyRedirects("round and round")

        with serving(odd, config, page) as provider:
            provider.propose(page)

            assert provider.typed_failure_counts == {"RAISED": 1}

    @pytest.mark.parametrize("status", [401, 403, 429, 500])
    def test_every_refusal_is_counted_as_one_kind(
        self, config: HostedProviderConfig, page: LinkProposalRequest, status: int
    ) -> None:
        """A rate limit and a bad key are one kind here, and not the wrong one.

        They are deliberately indistinguishable: keeping them apart would mean
        keeping the host's own explanation. Both must still be distinguishable
        from a host that could not be reached, which is the point.
        """
        with serving(lambda _r: httpx2.Response(status, json={}), config, page) as provider:
            provider.propose(page)

            assert provider.typed_failure_counts == {"REFUSED_BY_PROVIDER": 1}

    def test_an_empty_body_is_counted_as_one(
        self, config: HostedProviderConfig, page: LinkProposalRequest
    ) -> None:
        """Two hundred and nothing in it."""
        with serving(lambda _r: httpx2.Response(200, content=b""), config, page) as provider:
            provider.propose(page)

            assert provider.typed_failure_counts == {"RETURNED_NOTHING": 1}

    def test_an_oversized_response_is_counted_as_one(
        self, small: HostedProviderConfig, page: LinkProposalRequest
    ) -> None:
        """Counted from the streaming path, where the read was abandoned."""
        stream = RecordingStream(padded(200_000))

        with serving(streaming(stream), small, page) as provider:
            provider.propose(page)

            assert provider.typed_failure_counts == {"RESPONSE_TOO_LARGE": 1}

    def test_an_unreadable_response_is_counted_as_one(
        self, config: HostedProviderConfig, page: LinkProposalRequest
    ) -> None:
        """The envelope was not JSON the adapter understands."""
        with serving(lambda _r: httpx2.Response(200, content=b"<html>"), config, page) as provider:
            provider.propose(page)

            assert provider.typed_failure_counts == {"UNREADABLE_RESPONSE": 1}

    def test_an_exhausted_budget_is_counted_as_one(self, page: LinkProposalRequest) -> None:
        """A stop rather than a failure, and still worth seeing in a receipt."""
        config = HostedProviderConfig.from_environment(
            {**ENVIRONMENT, "SETTLEMENT_WITNESS_AI_MAX_REQUESTS": "1"}
        )

        with serving(answering(ABSTAIN), config, page) as provider:
            provider.propose(page)
            provider.propose(page)

            assert provider.typed_failure_counts == {"BUDGET_EXHAUSTED": 1}

    def test_an_unauthorised_page_is_counted_as_one(
        self, config: HostedProviderConfig, page: LinkProposalRequest
    ) -> None:
        """The one kind that never involved the host at all."""
        corpus = build_corpus()
        authorised = frozenset(
            one.request_fingerprint
            for one in build_requests(FactSnapshot.from_index(corpus.index), corpus.styling)
        )

        with HostedLinkProposalProvider(
            config,
            authorised_requests=authorised,
            transport=httpx2.MockTransport(answering(ABSTAIN)),
        ) as provider:
            provider.propose(page)

            assert provider.typed_failure_counts == {"REQUEST_NOT_AUTHORIZED": 1}

    def test_a_mixed_run_keeps_the_kinds_apart(
        self, config: HostedProviderConfig, page: LinkProposalRequest
    ) -> None:
        """Which is the whole reason for counting them separately."""
        answers = iter(
            [
                httpx2.Response(429, json={}),
                httpx2.Response(429, json={}),
                httpx2.Response(200, content=b""),
                completion(ABSTAIN),
            ]
        )

        def handler(_request: httpx2.Request) -> httpx2.Response:
            return next(answers)

        with serving(handler, config, page) as provider:
            for _ in range(4):
                provider.propose(page)

            assert provider.typed_failure_counts == {
                "REFUSED_BY_PROVIDER": 2,
                "RETURNED_NOTHING": 1,
            }

    def test_the_counts_carry_no_provider_text(
        self, config: HostedProviderConfig, page: LinkProposalRequest
    ) -> None:
        """Keys are kind names and values are integers. Nothing else fits."""

        def chatty(_request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(
                429,
                json={"error": f"rate limited, key {SECRET}"},
                headers={"x-request-id": SECRET, "retry-after": "60"},
            )

        with serving(chatty, config, page) as provider:
            provider.propose(page)
            counts = provider.typed_failure_counts

        assert counts == {"REFUSED_BY_PROVIDER": 1}
        assert SECRET not in json.dumps(counts)
        assert all(isinstance(value, int) for value in counts.values())

    def test_the_kinds_are_bounded_by_the_enum(
        self, config: HostedProviderConfig, page: LinkProposalRequest
    ) -> None:
        """A host cannot grow the counter, whatever it answers with."""

        def varied(request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(int(request.headers.get("x-status", "500")), json={})

        with serving(varied, config, page) as provider:
            for _ in range(50):
                provider.propose(page)
            counts = provider.typed_failure_counts

        assert set(counts) <= {kind.value for kind in FailureKind}
        assert len(counts) == 1


class TestTheEndpointCannotSmuggleASecret:
    """A base URL is copied into the provenance of every receipt.

    So it is held to the same standard as the key: the three places a token gets
    put in a URL are refused outright, and a refusal never quotes what it read.
    """

    LEAKY: Final = (
        "https://admin:hunter2@api.example.test/v1",
        "https://admin@api.example.test/v1",
        "https://:hunter2@api.example.test/v1",
        "https://api.example.test/v1?api_key=hunter2",
        "https://api.example.test/v1?token=hunter2&x=1",
        "https://api.example.test/v1#hunter2",
        "http://admin:hunter2@localhost:8080/v1",
    )

    @pytest.mark.parametrize("endpoint", LEAKY)
    def test_it_is_refused(self, endpoint: str) -> None:
        """User-info credentials, a query string, or a fragment."""
        with pytest.raises(MissingConfiguration):
            HostedProviderConfig.from_environment(
                {**ENVIRONMENT, "SETTLEMENT_WITNESS_AI_BASE_URL": endpoint}
            )

    @pytest.mark.parametrize("endpoint", LEAKY)
    def test_the_refusal_quotes_nothing(self, endpoint: str) -> None:
        """A message about a secret must not contain the secret.

        This is the failure mode the check exists to prevent, so the message is
        asserted rather than assumed: an error that helpfully echoed the URL it
        rejected would put the token in a terminal, a log and probably a ticket.
        """
        with pytest.raises(MissingConfiguration) as raised:
            HostedProviderConfig.from_environment(
                {**ENVIRONMENT, "SETTLEMENT_WITNESS_AI_BASE_URL": endpoint}
            )

        message = str(raised.value)

        assert "hunter2" not in message
        assert "admin" not in message
        assert endpoint not in message
        assert "SETTLEMENT_WITNESS_AI_BASE_URL" in message

    @pytest.mark.parametrize("endpoint", LEAKY)
    def test_direct_construction_is_refused_too(self, endpoint: str) -> None:
        """The guarantee is on the model, not on one way of building it.

        `MissingConfiguration` is a `RuntimeError` for exactly this reason.
        Pydantic wraps a `ValueError` raised in a validator into a message that
        quotes the input it was given, so a `ValueError` here would echo the URL
        on this path even though `from_environment` did not.
        """
        with pytest.raises(MissingConfiguration) as raised:
            HostedProviderConfig(
                base_url=endpoint,
                api_key=SecretStr(SECRET),
                model="some-model-1",
                timeout_seconds=20,
                max_response_bytes=20000,
                max_requests=50,
            )

        assert "hunter2" not in str(raised.value)

    def test_a_url_that_cannot_be_parsed_is_refused(self) -> None:
        """An unclosed IPv6 bracket, which urlparse itself rejects."""
        with pytest.raises(MissingConfiguration) as raised:
            HostedProviderConfig.from_environment(
                {**ENVIRONMENT, "SETTLEMENT_WITNESS_AI_BASE_URL": "https://[::1/v1"}
            )

        assert "not a URL that can be parsed" in str(raised.value)

    @pytest.mark.parametrize(
        "endpoint",
        [
            "https://api.example.test/v1",
            "https://api.example.test/openai/deployments/x",
            "http://localhost:8080/v1",
            "http://127.0.0.1:1234/v1",
        ],
    )
    def test_an_ordinary_endpoint_is_still_accepted(self, endpoint: str) -> None:
        """The check refuses what it should and nothing else."""
        config = HostedProviderConfig.from_environment(
            {**ENVIRONMENT, "SETTLEMENT_WITNESS_AI_BASE_URL": endpoint}
        )

        assert config.base_url == endpoint

    def test_the_provenance_of_a_clean_url_is_the_url(self) -> None:
        """Recorded verbatim, which is only safe because of the checks above."""
        config = HostedProviderConfig.from_environment(ENVIRONMENT)

        assert config.provenance()["base_url"] == "https://api.example.test/v1"

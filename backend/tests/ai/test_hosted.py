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
from collections.abc import Callable, Mapping

import httpx2
import pytest

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
    presentation_payload,
)
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
) -> HostedLinkProposalProvider:
    """Return a provider whose requests are served in process."""
    return HostedLinkProposalProvider(config, transport=httpx2.MockTransport(handler))


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

        with serving(refuse, config) as provider:
            result = provider.propose(page)

        assert isinstance(result, ProviderFailure)
        assert SECRET not in result.model_dump_json()

    def test_it_is_absent_from_a_run_receipt(
        self, config: HostedProviderConfig, snapshot: FactSnapshot
    ) -> None:
        """The artifact a person is most likely to paste somewhere."""
        from app.ai.evaluation import SHADOW_HARNESS_VERSION
        from app.ai.live_shadow import LiveShadowRunReceipt

        with serving(
            answering({"outcome": "ABSTAIN", "selected_source_record_ids": []}), config
        ) as provider:
            report = evaluate(snapshot, provider)
            receipt = LiveShadowRunReceipt(
                harness_version=SHADOW_HARNESS_VERSION,
                corpus_version="1.0.0",
                provider_name=provider.identity.name,
                model_id=config.model,
                configuration=config.provenance(),
                requests_made=provider.requests_made,
                failure_counts={},
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

        with serving(capture, config) as provider:
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

        with serving(capture, config) as provider:
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

        with serving(capture, config) as provider:
            provider.propose(page)

        body = seen["body"]
        assert body["temperature"] == TEMPERATURE == 0.0  # type: ignore[index]
        assert body["model"] == "some-model-1"  # type: ignore[index]
        assert body["response_format"]["type"] == "json_schema"  # type: ignore[index]

    def test_the_identity_names_the_adapter_and_the_model(
        self, config: HostedProviderConfig
    ) -> None:
        """So a report says which model produced it."""
        with serving(
            answering({"outcome": "ABSTAIN", "selected_source_record_ids": []}), config
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

        with serving(handler, config) as provider:
            raw = provider.propose(page)
            result = parse_proposal(raw, page, provider.identity)

        assert isinstance(result, ValidProposal)
        assert result.selected == set(chosen)

    def test_an_abstention_is_returned_unchanged(
        self, config: HostedProviderConfig, page: LinkProposalRequest
    ) -> None:
        """Declining is an answer here as everywhere else."""
        handler = answering({"outcome": "ABSTAIN", "selected_source_record_ids": []})

        with serving(handler, config) as provider:
            result = parse_proposal(provider.propose(page), page, provider.identity)

        assert isinstance(result, ValidProposal)
        assert result.abstained

    def test_the_bound_proposal_names_the_hosted_provider(
        self, config: HostedProviderConfig, page: LinkProposalRequest
    ) -> None:
        """Identity is read from the provider object, as it is for a fixture."""
        handler = answering({"outcome": "ABSTAIN", "selected_source_record_ids": []})

        with serving(handler, config) as provider:
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
        with serving(answering(content), config) as provider:
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
        with serving(answering(payload), config) as provider:
            result = parse_proposal(provider.propose(page), page, provider.identity)

        assert result.code is RejectionCode.MALFORMED  # type: ignore[union-attr]

    def test_an_unknown_record_id_is_rejected(
        self, config: HostedProviderConfig, page: LinkProposalRequest
    ) -> None:
        """A model cannot name a record it was not offered."""
        handler = answering(
            {"outcome": "PROPOSE", "selected_source_record_ids": ["PAYMENT_EVENT:invented"]}
        )

        with serving(handler, config) as provider:
            result = parse_proposal(provider.propose(page), page, provider.identity)

        assert result.code is RejectionCode.OUT_OF_CANDIDATE_SET  # type: ignore[union-attr]

    def test_a_duplicate_id_is_rejected(
        self, config: HostedProviderConfig, page: LinkProposalRequest
    ) -> None:
        """Not deduplicated on the way through."""
        chosen = sorted(page.candidate_ids)[0]
        handler = answering({"outcome": "PROPOSE", "selected_source_record_ids": [chosen, chosen]})

        with serving(handler, config) as provider:
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

        with serving(handler, config) as provider:
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

        with serving(handler, config) as provider:
            result = parse_proposal(provider.propose(pages[0]), pages[0], provider.identity)

        assert result.code is RejectionCode.OUT_OF_CANDIDATE_SET  # type: ignore[union-attr]


class TestFailuresAreTypedAndCarryNothing:
    """A host that did not answer, in each of the ways it can fail."""

    def test_a_timeout(self, config: HostedProviderConfig, page: LinkProposalRequest) -> None:
        """The configured timeout, not a retry loop."""

        def slow(_request: httpx2.Request) -> httpx2.Response:
            raise httpx2.TimeoutException("too slow")

        with serving(slow, config) as provider:
            result = provider.propose(page)

        assert isinstance(result, ProviderFailure)
        assert result.kind is FailureKind.TIMED_OUT

    def test_a_connection_failure(
        self, config: HostedProviderConfig, page: LinkProposalRequest
    ) -> None:
        """The endpoint could not be reached."""

        def unreachable(_request: httpx2.Request) -> httpx2.Response:
            raise httpx2.ConnectError("no route")

        with serving(unreachable, config) as provider:
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

        with serving(handler, config) as provider:
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

        with serving(handler, config) as provider:
            result = provider.propose(page)

        assert isinstance(result, ProviderFailure)
        assert result.kind is FailureKind.RESPONSE_TOO_LARGE

    def test_an_empty_body(self, config: HostedProviderConfig, page: LinkProposalRequest) -> None:
        """A host that answered with nothing did not answer."""
        handler = lambda _request: httpx2.Response(200, content=b"")  # noqa: E731

        with serving(handler, config) as provider:
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

        with serving(handler, config) as provider:
            result = provider.propose(page)

        assert isinstance(result, ProviderFailure)
        assert result.kind is FailureKind.UNREADABLE_RESPONSE

    def test_a_body_that_is_not_json(
        self, config: HostedProviderConfig, page: LinkProposalRequest
    ) -> None:
        """An HTML error page, for instance."""
        handler = lambda _request: httpx2.Response(200, content=b"<html>502</html>")  # noqa: E731

        with serving(handler, config) as provider:
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

        with serving(handler, config) as provider:
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

        with serving(handler, config) as provider:
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

        with serving(handler, config) as provider:
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

        with serving(handler, config) as provider:
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

        with serving(handler, config) as provider:
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

        with serving(odd, config) as provider:
            result = provider.propose(page)

        assert isinstance(result, ProviderFailure)
        assert result.kind is FailureKind.RAISED

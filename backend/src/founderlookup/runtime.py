"""Production-local composition root for the hackathon MVP.

The HTTP factory remains dependency-injectable for deterministic tests. This module is
the only place that chooses concrete local persistence, private byte storage, and the
narrow OCR adapter used by the executable ASGI application.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

import httpx
from fastapi import FastAPI

from founderlookup.api.app import create_app
from founderlookup.api.settings import APISettings
from founderlookup.application.screening_bridge import DeterministicScreeningBridge
from founderlookup.application.service import (
    ApplicationExtractionOutcome,
    ApplicationServiceError,
    FakeVCBrainService,
)
from founderlookup.application.sourcing import (
    MultiAdapterSourcingCoordinator,
    SourceAdapterBinding,
    SourcingCoordinatorPort,
    UnavailableSourcingCoordinator,
)
from founderlookup.demo.bootstrap import DemoBootstrapResult, seed_local_demo
from founderlookup.domain.common import KnowledgeValue
from founderlookup.domain.evidence import SourceArtifactKind, SourceCategory
from founderlookup.infrastructure.artifacts import PrivateArtifactStore
from founderlookup.infrastructure.deck_evidence import SQLiteDeckEvidenceStore
from founderlookup.infrastructure.intake_repository import SQLiteIntakeRepository
from founderlookup.infrastructure.persistence import SQLiteMemory, new_opaque_id
from founderlookup.infrastructure.rule_overrides import SQLiteRuleOverrideLedger
from founderlookup.ingestion.extraction import (
    PdfExtractionBlockedError,
    PdfExtractionRequest,
    PdfExtractionResult,
    PdfExtractor,
)
from founderlookup.ingestion.intake import (
    ApplicationIntakeService,
    ExtractionAttemptStatus,
    IntakeServiceError,
)
from founderlookup.ingestion.mistral_ocr import (
    MistralOcrConfigurationError,
    MistralOcrExtractor,
    MistralOcrSettings,
)
from founderlookup.ingestion.policy import PublicSourceCollectionPolicy
from founderlookup.ingestion.sources.github import GitHubDeveloperActivitySource
from founderlookup.ingestion.sources.hackernews import HackerNewsSocialSource
from founderlookup.ingestion.sources.http import HttpTransport, UrllibHttpTransport
from founderlookup.ingestion.sources.openalex import OpenAlexResearchSource
from founderlookup.ingestion.sources.patentsview import PatentsViewPatentSource
from founderlookup.ingestion.sources.semanticscholar import SemanticScholarResearchSource
from founderlookup.ingestion.tavily import TavilyPolicy, TavilySource
from founderlookup.screening.query_executor import DeterministicQueryExecutor
from founderlookup.screening.query_planner import DeterministicQueryPlanner

_EXTRACTION_PRINCIPAL = "system:pdf-extraction"
_INVESTOR_PRINCIPAL = "investor"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _opaque_id(prefix: str) -> str:
    return f"{prefix}:{new_opaque_id()}"


def _absolute_data_dir(configured: Path) -> Path:
    candidate = configured.expanduser()
    if candidate.is_symlink():
        raise ValueError("data directory cannot be a symbolic link")
    absolute = candidate if candidate.is_absolute() else Path.cwd() / candidate
    absolute = absolute.absolute()
    if absolute == Path(absolute.anchor):
        raise ValueError("data directory cannot be a filesystem root")
    return absolute


def _environment_bool(value: bool) -> str:
    return "true" if value else "false"


def _ocr_environment(settings: APISettings) -> dict[str, str] | None:
    if settings.mistral_api_key is None:
        return None
    values = {
        "MISTRAL_API_KEY": settings.mistral_api_key.get_secret_value(),
        "FOUNDERLOOKUP_MISTRAL_OCR_ENABLED": _environment_bool(settings.mistral_ocr_enabled),
        "FOUNDERLOOKUP_MISTRAL_OCR_MODEL": settings.mistral_ocr_model,
        "FOUNDERLOOKUP_MISTRAL_OCR_MAX_INPUT_BYTES": str(settings.mistral_ocr_max_input_bytes),
        "FOUNDERLOOKUP_MISTRAL_OCR_MAX_RESPONSE_BYTES": str(
            settings.mistral_ocr_max_response_bytes
        ),
        "FOUNDERLOOKUP_MISTRAL_OCR_MAX_PAGES": str(settings.mistral_ocr_max_pages),
        "FOUNDERLOOKUP_MISTRAL_OCR_TIMEOUT_SECONDS": str(settings.mistral_ocr_timeout_seconds),
        "FOUNDERLOOKUP_MISTRAL_OCR_APPROVED_NON_PRIVATE_CLASSIFICATIONS": (
            settings.mistral_ocr_approved_non_private_classifications
        ),
        "FOUNDERLOOKUP_MISTRAL_OCR_ALLOW_PRIVATE": _environment_bool(
            settings.mistral_ocr_allow_private
        ),
        "FOUNDERLOOKUP_MISTRAL_OCR_TRAINING_OPT_OUT_CONFIRMED": _environment_bool(
            settings.mistral_ocr_training_opt_out_confirmed
        ),
        "FOUNDERLOOKUP_MISTRAL_OCR_RETENTION_POSTURE": (settings.mistral_ocr_retention_posture),
        "FOUNDERLOOKUP_MISTRAL_OCR_REGION_CONFIRMED": _environment_bool(
            settings.mistral_ocr_region_confirmed
        ),
        "FOUNDERLOOKUP_MISTRAL_OCR_PURPOSE_CONFIRMED": _environment_bool(
            settings.mistral_ocr_purpose_confirmed
        ),
    }
    if settings.mistral_ocr_region is not None:
        values["FOUNDERLOOKUP_MISTRAL_OCR_REGION"] = settings.mistral_ocr_region
    if settings.mistral_ocr_purpose is not None:
        values["FOUNDERLOOKUP_MISTRAL_OCR_PURPOSE"] = settings.mistral_ocr_purpose
    return values


class _ConfigurationBlockedExtractor:
    """Fail closed while leaving intake and explicit Unknown values available."""

    async def extract(self, _request: PdfExtractionRequest) -> PdfExtractionResult:
        raise MistralOcrConfigurationError


class _PerRequestMistralExtractor:
    """Own the short-lived HTTP client used for one bounded OCR operation."""

    def __init__(self, settings: MistralOcrSettings) -> None:
        self._settings = settings

    async def extract(self, request: PdfExtractionRequest) -> PdfExtractionResult:
        async with httpx.AsyncClient(trust_env=False) as client:
            extractor = MistralOcrExtractor(
                settings=self._settings,
                client=client,
                clock=_utc_now,
                id_factory=_opaque_id,
            )
            return await extractor.extract(request)


class _ApplicationExtractionCoordinator:
    """Coalesce one in-flight extraction while allowing a later replay to recover."""

    def __init__(self, runner: Callable[[str], Awaitable[None]]) -> None:
        self._runner = runner
        self._claim_lock = asyncio.Lock()
        self._inflight: dict[str, asyncio.Event] = {}

    async def __call__(self, application_id: str) -> None:
        async with self._claim_lock:
            completion = self._inflight.get(application_id)
            if completion is None:
                completion = asyncio.Event()
                self._inflight[application_id] = completion
                owns_claim = True
            else:
                owns_claim = False

        if not owns_claim:
            await completion.wait()
            return

        try:
            # ApplicationIntakeService rechecks durable accepted output after this claim.
            await self._runner(application_id)
        finally:
            completion.set()
            async with self._claim_lock:
                if self._inflight.get(application_id) is completion:
                    del self._inflight[application_id]


def _configured_extractor(settings: APISettings) -> PdfExtractor:
    environment = _ocr_environment(settings)
    if environment is None:
        return _ConfigurationBlockedExtractor()
    try:
        ocr_settings = MistralOcrSettings.from_environment(environ=environment)
    except MistralOcrConfigurationError:
        return _ConfigurationBlockedExtractor()
    return _PerRequestMistralExtractor(ocr_settings)


def _public_source_policy(source_name: str) -> PublicSourceCollectionPolicy:
    """Keep unreviewed terms and robots facts explicitly Unknown at composition time."""

    return PublicSourceCollectionPolicy(
        collection_purpose="investor sourcing and evaluation",
        lawful_basis="legitimate interests with human review and removal",
        source_terms=KnowledgeValue[str].unknown(
            f"{source_name} terms have not been recorded in runtime configuration"
        ),
        robots_policy=KnowledgeValue[str].unknown(
            f"{source_name} robots policy has not been recorded in runtime configuration"
        ),
    )


def create_runtime_app(
    *,
    settings: APISettings | None = None,
    extractor: PdfExtractor | None = None,
    tavily_client: httpx.AsyncClient | None = None,
    public_source_transport: HttpTransport | None = None,
    clock: Callable[[], datetime] = _utc_now,
) -> FastAPI:
    """Compose the executable API with durable local and fail-closed adapters."""

    configured = settings or APISettings()
    logging.getLogger("founderlookup").setLevel(configured.log_level)
    data_dir = _absolute_data_dir(configured.data_dir)

    intake_repository = SQLiteIntakeRepository(data_dir / "intake.sqlite3", clock=clock)
    memory = SQLiteMemory(data_dir / "memory.sqlite3")
    deck_evidence_store = SQLiteDeckEvidenceStore(memory)
    artifact_store = PrivateArtifactStore(
        data_dir / "artifacts",
        authorize_read=lambda principal_id, _artifact_id: (
            principal_id in {_INVESTOR_PRINCIPAL, _EXTRACTION_PRINCIPAL}
        ),
    )
    intake = ApplicationIntakeService(
        repository=intake_repository,
        artifact_store=artifact_store,
        extractor=extractor or _configured_extractor(configured),
        clock=clock,
        id_factory=_opaque_id,
        max_pdf_bytes=configured.maximum_deck_bytes,
        extraction_principal_id=_EXTRACTION_PRINCIPAL,
    )
    screening_bridge = DeterministicScreeningBridge()
    query_planner = DeterministicQueryPlanner(clock=clock)
    service = FakeVCBrainService(
        clock=clock,
        capability_pepper=configured.resolved_status_pepper(),
        max_retry_attempts=configured.maximum_retry_attempts,
        artifact_reader=artifact_store,
        query_executor=DeterministicQueryExecutor(
            maximum_results=configured.maximum_collection_results
        ),
        rule_override_ledger=SQLiteRuleOverrideLedger(memory),
        screening_bridge=screening_bridge,
    )
    demo_bootstrap: DemoBootstrapResult | None = None
    if configured.demo_seed_enabled:
        demo_bootstrap = seed_local_demo(service, screening_bridge=screening_bridge)

    adapters: list[SourceAdapterBinding] = []
    sourcing_coordinator: SourcingCoordinatorPort
    if configured.tavily_enabled:
        assert configured.tavily_api_key is not None  # settings fail closed before composition
        tavily_source = TavilySource(
            api_key=configured.tavily_api_key,
            policy=TavilyPolicy(
                max_queries=configured.tavily_max_queries,
                max_results=configured.tavily_max_results,
                max_pages=configured.tavily_max_pages,
                max_content_bytes=configured.tavily_max_content_bytes,
                max_response_bytes=configured.tavily_max_response_bytes,
                timeout_seconds=configured.tavily_timeout_seconds,
                allowed_domains=configured.tavily_allowed_domain_list,
                excluded_domains=configured.tavily_excluded_domain_list,
            ),
            now=clock,
            client=tavily_client,
        )
        adapters.append(
            SourceAdapterBinding(
                adapter_id=tavily_source.adapter_id,
                discovery=tavily_source,
                acquisition=tavily_source,
                source_categories=None,
                authoritative=False,
                artifact_kind=SourceArtifactKind.WEB_SNAPSHOT,
                allowed_media_types=("text/markdown", "text/plain"),
                policy=_public_source_policy("Original public web source"),
            )
        )

    source_specific_enabled = any(
        (
            configured.github_enabled,
            configured.hackernews_enabled,
            configured.openalex_enabled,
            configured.semantic_scholar_enabled,
            configured.patentsview_enabled,
        )
    )
    source_transport = (
        public_source_transport or UrllibHttpTransport()
        if source_specific_enabled
        else None
    )
    if configured.github_enabled:
        assert source_transport is not None
        github = GitHubDeveloperActivitySource(
            source_transport,
            now=clock,
            token=(
                configured.github_token.get_secret_value()
                if configured.github_token is not None
                else None
            ),
        )
        adapters.append(
            SourceAdapterBinding(
                adapter_id=github.adapter_id,
                discovery=github,
                acquisition=github,
                source_categories=(SourceCategory.DEVELOPER_ACTIVITY,),
                authoritative=True,
                artifact_kind=SourceArtifactKind.REPOSITORY_RECORD,
                allowed_media_types=("application/json",),
                policy=_public_source_policy("GitHub public API"),
            )
        )
    if configured.hackernews_enabled:
        assert source_transport is not None
        hackernews = HackerNewsSocialSource(source_transport, now=clock)
        adapters.append(
            SourceAdapterBinding(
                adapter_id=hackernews.adapter_id,
                discovery=hackernews,
                acquisition=hackernews,
                source_categories=(SourceCategory.PUBLIC_SOCIAL,),
                authoritative=True,
                artifact_kind=SourceArtifactKind.SOURCE_API_RECORD,
                allowed_media_types=("application/json",),
                policy=_public_source_policy("Hacker News public API"),
            )
        )
    if configured.openalex_enabled:
        assert source_transport is not None
        openalex = OpenAlexResearchSource(source_transport, now=clock)
        adapters.append(
            SourceAdapterBinding(
                adapter_id=openalex.adapter_id,
                discovery=openalex,
                acquisition=openalex,
                source_categories=(SourceCategory.RESEARCH,),
                authoritative=True,
                artifact_kind=SourceArtifactKind.SOURCE_API_RECORD,
                allowed_media_types=("application/json",),
                policy=_public_source_policy("OpenAlex public API"),
            )
        )
    if configured.semantic_scholar_enabled:
        assert source_transport is not None
        semantic_scholar = SemanticScholarResearchSource(source_transport, now=clock)
        adapters.append(
            SourceAdapterBinding(
                adapter_id=semantic_scholar.adapter_id,
                discovery=semantic_scholar,
                acquisition=semantic_scholar,
                source_categories=(SourceCategory.RESEARCH,),
                authoritative=True,
                artifact_kind=SourceArtifactKind.SOURCE_API_RECORD,
                allowed_media_types=("application/json",),
                policy=_public_source_policy("Semantic Scholar public API"),
            )
        )
    if configured.patentsview_enabled:
        assert source_transport is not None
        patentsview = PatentsViewPatentSource(source_transport, now=clock)
        adapters.append(
            SourceAdapterBinding(
                adapter_id=patentsview.adapter_id,
                discovery=patentsview,
                acquisition=patentsview,
                source_categories=(SourceCategory.PATENT,),
                authoritative=True,
                artifact_kind=SourceArtifactKind.SOURCE_API_RECORD,
                allowed_media_types=("application/json",),
                policy=_public_source_policy("PatentsView public API"),
            )
        )

    if adapters:
        sourcing_coordinator = MultiAdapterSourcingCoordinator(
            adapters=tuple(adapters),
            service=service,
            memory=memory,
            artifact_store=artifact_store,
            screening_bridge=screening_bridge,
            now=clock,
            id_factory=_opaque_id,
            max_results=configured.sourcing_max_results,
            max_pages=configured.sourcing_max_pages,
            max_bytes=configured.sourcing_max_content_bytes,
            timeout_seconds=configured.sourcing_timeout_seconds,
            cache_ttl_seconds=configured.sourcing_cache_ttl_seconds,
        )
    else:
        sourcing_coordinator = UnavailableSourcingCoordinator()

    def record_safe_failure(application_id: str, error: Exception | None) -> None:
        outcome = ApplicationExtractionOutcome.FAILED
        safe_code = (
            error.code
            if isinstance(error, PdfExtractionBlockedError | IntakeServiceError)
            else "deck_extraction_failed"
        )
        try:
            record = intake_repository.get_application(application_id)
            if record is not None and record.extraction_attempts:
                attempt = record.extraction_attempts[-1]
                safe_code = attempt.safe_code
                if attempt.status is ExtractionAttemptStatus.BLOCKED:
                    outcome = ApplicationExtractionOutcome.BLOCKED
        except Exception:
            # Persistence failures remain a generic safe extraction failure at the API seam.
            pass
        try:
            service.record_application_extraction_outcome(
                application_id,
                outcome=outcome,
                safe_code=safe_code,
            )
        except (ApplicationServiceError, ValueError):
            # A missing process-local projection cannot expose private background details.
            return

    async def extract_once(application_id: str) -> None:
        try:
            extraction = await intake.extract_deck(application_id)
        except (IntakeServiceError, PdfExtractionBlockedError) as error:
            # Intake persists the provider-neutral attempt before publishing run state.
            record_safe_failure(application_id, error)
            return
        except Exception:
            record_safe_failure(application_id, None)
            return
        try:
            intake_record = intake_repository.get_application(application_id)
            if intake_record is None:
                raise RuntimeError("accepted Application intake record is unavailable")
            projection = service.project_application_deck(
                application_id,
                extraction=extraction,
                source_artifact=intake_record.source_artifact,
            )
            deck_evidence_store.persist(
                source_artifact=intake_record.source_artifact,
                projection=projection,
            )
            service.register_deck_evidence_projection(projection)
            service.record_application_extraction_outcome(
                application_id,
                outcome=ApplicationExtractionOutcome.SUCCEEDED,
                accepted_output_id=extraction.extraction_id,
                additional_output_ids=(projection.projection_id,),
            )
        except Exception:
            with suppress(ApplicationServiceError, ValueError):
                service.record_application_extraction_outcome(
                    application_id,
                    outcome=ApplicationExtractionOutcome.FAILED,
                    safe_code="deck_evidence_projection_failed",
                )
            return

    extraction_coordinator = _ApplicationExtractionCoordinator(extract_once)

    application = create_app(
        settings=configured,
        service=service,
        intake_service=intake,
        application_extraction=extraction_coordinator,
        sourcing_coordinator=sourcing_coordinator,
        query_planner=query_planner,
    )
    # Deliberately internal handles for process diagnostics and deterministic integration tests.
    application.state.intake_repository = intake_repository
    application.state.private_artifact_store = artifact_store
    application.state.sqlite_memory = memory
    application.state.deck_evidence_store = deck_evidence_store
    application.state.application_extraction_coordinator = extraction_coordinator
    application.state.deterministic_screening_bridge = screening_bridge
    application.state.demo_bootstrap = demo_bootstrap
    application.state.enabled_sourcing_adapters = tuple(
        binding.adapter_id for binding in adapters
    )
    application.state.deterministic_query_planner = query_planner
    return application


__all__ = ["create_runtime_app"]

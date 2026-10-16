"""Persistence backend selection for the product API (ODP-PV-009).

``build_persistence()`` is the single construction point for the repositories,
audit log, and job queue that ``apps/api`` wires into ``create_app``. The
backend is chosen by environment so the *same* default code path can run either
in-memory (unit tests, fast local boot) or against durable SQLite storage
(Product-Grade E2E, where writes must survive a process restart):

    ODP_PERSISTENCE = memory              -> in-memory implementations
    ODP_PERSISTENCE = durable | sqlite    -> SQLite local/test implementations
    ODP_PERSISTENCE = postgresql | postgres -> PostgreSQL production runtime
    ODP_DB_PATH     = <path>              -> local SQLite database file
    ODAY_DATABASE_URL = postgresql://...  -> production PostgreSQL database

In ``memory`` mode the bundle holds exactly the implementations the API used
before this task, so default behaviour is unchanged.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shared.audit.worm import AuditWormSink, build_audit_worm_sink_from_env
from shared.governance import (
    InMemoryDecisionPolicyRepository,
    default_heatzone_absorption_policy,
    default_heatzone_merge_policy,
    default_model_performance_drift_policy,
)

DEFAULT_DB_PATH = ".odp_data/durable.sqlite3"
_DURABLE_MODES = {"durable", "sqlite"}
_MEMORY_MODES = {"memory"}
_POSTGRES_MODES = {"postgres", "postgresql"}
_SUPPORTED_MODES = _MEMORY_MODES | _DURABLE_MODES | _POSTGRES_MODES
_TRUTHY_ENV_VALUES = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class PersistenceBundle:
    """The set of storage-backed collaborators injected into ``create_app``."""

    mode: str
    audit_log: Any
    evidence_store: Any
    job_queue: Any
    avm_repository: Any
    forecastops_repository: Any
    netplan_repository: Any
    learninghub_repository: Any
    artifact_store: Any
    priceops_repository: Any
    sitescore_repository: Any
    adlift_repository: Any
    store_ops_repository: Any
    intervention_repository: Any
    intervention_label_registry: Any
    ingestion_run_store: Any
    # Expansion decision-flow stores (ODP-FLOW-002, ODP-HZ006-MERGE-SPLIT-IMPLEMENTATION-001):
    # HeatZone ranking, composition lineage/override, listing dedup + candidate inbox,
    # SiteScore decisions, and realized sites.
    heatzone_store: Any
    listing_repository: Any
    sitescore_decision_store: Any
    sitescore_realized_store: Any

    tenant_repository: Any
    brand_repository: Any
    address_location_repository: Any
    store_repository: Any
    machine_repository: Any
    transaction_repository: Any
    machine_cycle_repository: Any
    heatzone_composition_repository: Any = None
    heatzone_evidence_repository: Any = None
    heatzone_absorption_outcome_writer: Any = None
    manual_correction_repository: Any = None
    external_fetch_state_store: Any = None
    notification_repository: Any = None
    outbox_repository: Any = None
    operator_intake_repository: Any = None
    assisted_intake_store: Any = None
    assisted_intake_workflow_policy: Any = None
    engine: Any = None
    # Auth identity/session stores (ODP-WEB-LOCAL-AUTH-API-TRUST-001).
    # In PostgreSQL mode these are durable (SqlIdentityStore + SessionService);
    # in memory/SQLite mode they are in-memory doubles so the boundary always
    # has an identity_store and session_service wired rather than None.
    identity_store: Any = None
    session_service: Any = None
    # Decision-policy registry binding is supplied by the deployment that
    # owns workflow.decision_policies. Keeping it optional preserves the
    # memory/SQLite construction path while allowing API and worker callers to
    # share one registry repository when it is available.
    forecastops_policy_repository: Any = None
    current_model_admission: Any = None
    feature_flag_registry: Any = None
    adlift_campaign_repository: Any = None
    survey_assignment_repository: Any = None


    @property
    def is_durable(self) -> bool:
        return self.engine is not None

    @property
    def is_production(self) -> bool:
        return self.mode == "postgresql" and bool(
            getattr(self.engine, "is_production", False)
        )

    def score_execution_service(self, *, model_runtime: Any = None) -> Any:
        from models.shared_ml.current_model_admission import CurrentModelAdmissionPort
        from modules.sitescore.application.score_delivery import ScoreExecutionJobRepository
        from modules.sitescore.application.score_execution import SiteScoreExecutionService
        admission = self.current_model_admission
        if admission is None:
            admission = CurrentModelAdmissionPort(runtime=model_runtime,
                registry_repository=self.learninghub_repository, engine=self.engine)
        return SiteScoreExecutionService(ScoreExecutionJobRepository(self, model_admission=admission))

    def network_review_resolver(self, *, hooks: Any = ()) -> Any:
        """Native aliases share the Operator review domain's durable owner."""
        from apps.api.app.routes.operator_modules.live_service import DurableTenantServiceResolver
        from modules.opsboard.application.network_listings import NetworkListingService
        from modules.opsboard.application.network_reviews import NetworkReviewService
        from shared.audit.durable_delivery import DurableAuditDelivery
        from shared.infrastructure.persistence.document_store import SqliteDocumentStore
        from shared.infrastructure.persistence.operator_domains import (
            DurableOperatorDomainStateRepository,
            TenantScopedDocumentStore,
        )
        from shared.infrastructure.persistence.operator_network_listings import (
            DurableAssistedIntakeRepository,
        )
        from shared.infrastructure.persistence.repositories import (
            DurableApprovalRepository,
            DurableDecisionStore,
            DurableListingRepository,
            DurableSiteScoreRepository,
        )
        from shared.workflow.sitescore import SiteScoreDecisionWorkflow
        if not self.is_durable:
            return None
        store = SqliteDocumentStore(self.engine)
        def factory(state: Any, tenant: str) -> Any:
            scoped = TenantScopedDocumentStore(store, tenant)
            decisions = DurableDecisionStore(scoped)
            candidate = NetworkListingService(listing_repository=DurableListingRepository(scoped),
                intake_repository=DurableAssistedIntakeRepository(scoped), tenant_id=tenant,
                review_decision_repository=decisions, seed_fixtures=False)
            return NetworkReviewService(initial_state=state, seed_fixtures=False, require_canonical=True,
                decision_repository=decisions, sitescore_repository=DurableSiteScoreRepository(scoped),
                decision_workflow=SiteScoreDecisionWorkflow(store=decisions, audit_log=self.audit_log,
                    approval_store=DurableApprovalRepository(scoped), hooks=hooks),
                candidate_workflow_service=candidate,
                feature_flags=self.feature_flag_registry, job_queue=self.job_queue,
                audit_delivery_factory=lambda tid: DurableAuditDelivery(self.engine, tid, self.job_queue, self.audit_log._worm_sink))
        return DurableTenantServiceResolver(DurableOperatorDomainStateRepository(store, "network-reviews"),
            factory=factory, exporter=lambda value: value.export_state(),
            mutating_methods={"submit_review", "submit_native_report", "decide_review", "decide_native_review"}, transactional_mutations=True)

    def survey_assignment_repository_for_tenant(self, tenant_id: str) -> Any | None:
        repository = self.survey_assignment_repository
        return None if repository is None else repository.for_tenant(tenant_id)

    def listing_repository_for_tenant(self, tenant_id: str) -> Any | None:
        return self._scoped_repository("listing_repository", tenant_id)

    def operator_intake_repository_for_tenant(self, tenant_id: str) -> Any | None:
        return self._scoped_repository("operator_intake_repository", tenant_id)

    def sitescore_decision_store_for_tenant(self, tenant_id: str) -> Any | None:
        return self._scoped_repository("sitescore_decision_store", tenant_id)

    def ingestion_run_store_for_tenant(self, tenant_id: str) -> Any | None:
        return self._scoped_repository("ingestion_run_store", tenant_id)

    def heatzone_store_for_tenant(self, tenant_id: str) -> Any | None:
        return self._scoped_repository("heatzone_store", tenant_id)

    def heatzone_composition_repository_for_tenant(self, tenant_id: str) -> Any | None:
        return self._scoped_repository("heatzone_composition_repository", tenant_id)

    def heatzone_absorption_outcome_writer_for_tenant(self, tenant_id: str) -> Any | None:
        # Like the reader, the writer takes tenant_id on every call and touches
        # relations that are not document-store backed, so one writer per engine
        # is correct and a scoped wrapper would only hide the tenant argument.
        return self.heatzone_absorption_outcome_writer

    def heatzone_evidence_repository_for_tenant(self, tenant_id: str) -> Any | None:
        # The evidence reader already takes tenant_id on every call and reads
        # relations that are not document-store backed, so it needs no scoped
        # wrapper -- returning it unchanged keeps one reader per engine.
        return self.heatzone_evidence_repository

    def _scoped_repository(self, attribute: str, tenant_id: str) -> Any | None:
        if not tenant_id or not tenant_id.strip():
            return None
        repo = getattr(self, attribute, None)
        if repo is None:
            return None
        clean_tenant = tenant_id.strip()
        if str(getattr(repo, "tenant_id", "") or "").strip() == clean_tenant:
            return repo
        store = getattr(repo, "_store", None)
        if store is not None:
            base_store = getattr(store, "_store", store)
            if (
                hasattr(base_store, "get")
                and hasattr(base_store, "put")
                and hasattr(base_store, "list_all")
            ):
                from shared.infrastructure.persistence.operator_domains import (
                    TenantScopedDocumentStore,
                )

                return type(repo)(TenantScopedDocumentStore(base_store, clean_tenant))
        return None


def _default_decision_policy_repository() -> InMemoryDecisionPolicyRepository:
    from modules.forecastops.domain.forecasting import default_forecast_alert_policy
    from shared.governance import default_netplan_disclosure_policy

    seeded_tenants = (
        "tenant-test",
        "tenant-a",
        "tenant-b",
        "tenant-default",
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
        "00000000-0000-0000-0000-000000000001",
        "tenant-gate",
    )
    return InMemoryDecisionPolicyRepository(
        [
            policy
            for tenant_id in seeded_tenants
            for policy in (
                default_forecast_alert_policy(tenant_id),
                default_model_performance_drift_policy(tenant_id),
                default_heatzone_merge_policy(tenant_id),
                default_heatzone_absorption_policy(tenant_id),
                # One registry, keyed by policy_kind. NetPlan approval refuses
                # outright when its kind does not resolve, so a bundle that
                # seeds the other kinds and not this one would leave every
                # network plan unapprovable rather than merely ungoverned.
                default_netplan_disclosure_policy(tenant_id),
            )
        ]
    )


def _memory_bundle(worm_sink: AuditWormSink | None = None) -> PersistenceBundle:
    from models.shared_ml.artifact_store import InMemoryArtifactStore
    from modules.adlift.infrastructure import InMemoryAdLiftRepository
    from modules.avm.infrastructure import InMemoryAVMRepository
    from modules.external_data.application.ingestion_store import (
        InMemoryIngestionRunStore,
    )
    from modules.external_data.workers.scheduled_fetch import InMemoryExternalFetchStateStore
    from modules.forecastops.infrastructure import InMemoryForecastOpsRepository
    from modules.heatzone.infrastructure import (
        HeatZoneResultStore,
        InMemoryHeatZoneCompositionRepository,
        InMemoryMergeSplitEvidenceRepository,
    )
    from modules.intervention.infrastructure.repositories import (
        InMemoryInterventionRepository,
        InMemoryLabelRegistry,
    )
    from modules.learninghub.infrastructure import InMemoryLearningHubRepository
    from modules.listing.infrastructure.repositories import InMemoryListingRepository
    from modules.netplan.infrastructure import InMemoryNetPlanRepository
    from modules.notifications import InMemoryNotificationRepository
    from modules.opsboard.application.store_ops import InMemoryStoreOpsRepository
    from modules.priceops.infrastructure import InMemoryPriceOpsRepository
    from modules.sitescore.infrastructure.repositories import InMemorySiteScoreRepository
    from shared.audit.events import InMemoryAuditLog
    from shared.audit.persistence import InMemoryEvidenceBundleStore
    from shared.identity import (
        InMemoryIdentityStore,
        InMemorySessionRepository,
        SessionConfig,
        SessionService,
    )
    from shared.infrastructure.persistence.outbox import InMemoryOutboxRepository
    from shared.infrastructure.persistence.repositories import (
        InMemoryAddressLocationRepository,
        InMemoryBrandRepository,
        InMemoryMachineCycleRepository,
        InMemoryMachineRepository,
        InMemoryManualCorrectionRepository,
        InMemoryStoreRepository,
        InMemoryTenantRepository,
        InMemoryTransactionRepository,
    )
    from shared.jobs.queue import InMemoryJobQueue
    from shared.workflow.sitescore import InMemoryDecisionStore, InMemoryRealizedSiteStore

    # One object serves as both the evidence reader and the outcome writer here;
    # the durable bundle keeps them apart, but in memory a second instance would
    # simply be a second, empty history.
    _memory_heatzone_evidence = InMemoryMergeSplitEvidenceRepository()

    mem_identity_store = InMemoryIdentityStore()
    mem_session_service = SessionService(
        repository=InMemorySessionRepository(),
        config=SessionConfig(),
    )
    mem_corr_repo = InMemoryManualCorrectionRepository()
    mem_audit_log = InMemoryAuditLog(worm_sink=worm_sink)
    from shared.auth.feature_flags import default_registry
    memory_flags = default_registry()

    return PersistenceBundle(
        mode="memory",
        audit_log=mem_audit_log,
        evidence_store=InMemoryEvidenceBundleStore(worm_sink=worm_sink),
        job_queue=InMemoryJobQueue(flags=memory_flags),
        feature_flag_registry=memory_flags,
        avm_repository=InMemoryAVMRepository(),
        forecastops_repository=InMemoryForecastOpsRepository(),
        forecastops_policy_repository=_default_decision_policy_repository(),
        netplan_repository=InMemoryNetPlanRepository(),
        learninghub_repository=InMemoryLearningHubRepository(),
        artifact_store=InMemoryArtifactStore(),
        priceops_repository=InMemoryPriceOpsRepository(),
        sitescore_repository=InMemorySiteScoreRepository(),
        adlift_repository=InMemoryAdLiftRepository(),
        store_ops_repository=InMemoryStoreOpsRepository(),
        intervention_repository=InMemoryInterventionRepository(),
        intervention_label_registry=InMemoryLabelRegistry(),
        ingestion_run_store=InMemoryIngestionRunStore(),
        heatzone_store=HeatZoneResultStore(),
        heatzone_composition_repository=InMemoryHeatZoneCompositionRepository(),
        heatzone_evidence_repository=_memory_heatzone_evidence,
        heatzone_absorption_outcome_writer=_memory_heatzone_evidence,
        listing_repository=InMemoryListingRepository(),
        sitescore_decision_store=InMemoryDecisionStore(),
        sitescore_realized_store=InMemoryRealizedSiteStore(),

        tenant_repository=InMemoryTenantRepository(),
        brand_repository=InMemoryBrandRepository(),
        address_location_repository=InMemoryAddressLocationRepository(
            _corrections=mem_corr_repo
        ),
        manual_correction_repository=mem_corr_repo,
        store_repository=InMemoryStoreRepository(),
        machine_repository=InMemoryMachineRepository(),
        transaction_repository=InMemoryTransactionRepository(),
        machine_cycle_repository=InMemoryMachineCycleRepository(),
        external_fetch_state_store=InMemoryExternalFetchStateStore(),
        notification_repository=InMemoryNotificationRepository(),
        outbox_repository=InMemoryOutboxRepository(),
        identity_store=mem_identity_store,
        session_service=mem_session_service,
    )


def _durable_bundle(
    db_path: str | Path, *, worm_sink: AuditWormSink | None = None
) -> PersistenceBundle:
    from modules.adlift.infrastructure.campaign_repository import DurableAdLiftCampaignRepository
    from modules.external_data.workers.scheduled_fetch import DurableExternalFetchStateStore
    from modules.market_survey.infrastructure.assignment_repository import (
        DurableSurveyAssignmentRepository,
    )
    from modules.notifications import DurableNotificationRepository
    from modules.opsboard.application.store_ops import DurableStoreOpsRepository
    from modules.opsboard.audit.evidence_store import DurableEvidenceBundleStore
    from shared.infrastructure.persistence.audit_log import DurableAuditLog
    from shared.infrastructure.persistence.document_store import SqliteDocumentStore
    from shared.infrastructure.persistence.engine import SqliteEngine
    from shared.infrastructure.persistence.external_data import DurableIngestionRunStore
    from shared.infrastructure.persistence.job_queue import DurableJobQueue
    from shared.infrastructure.persistence.operator_network_listings import (
        DurableAssistedIntakeRepository,
    )
    from shared.infrastructure.persistence.outbox import DurableOutboxRepository
    from shared.infrastructure.persistence.repositories import (
        DurableAbsorptionOutcomeWriter,
        DurableAddressLocationRepository,
        DurableAdLiftRepository,
        DurableArtifactStore,
        DurableAVMRepository,
        DurableBrandRepository,
        DurableDecisionStore,
        DurableForecastOpsRepository,
        DurableHeatZoneCompositionRepository,
        DurableHeatZoneResultStore,
        DurableInterventionRepository,
        DurableLabelRegistry,
        DurableLearningHubRepository,
        DurableListingRepository,
        DurableMachineCycleRepository,
        DurableMachineRepository,
        DurableManualCorrectionRepository,
        DurableMergeSplitEvidenceRepository,
        DurableNetPlanRepository,
        DurablePriceOpsRepository,
        DurableRealizedSiteStore,
        DurableSiteScoreRepository,
        DurableStoreRepository,
        DurableTenantRepository,
        DurableTransactionRepository,
    )

    engine = SqliteEngine(db_path)
    store = SqliteDocumentStore(engine)
    from shared.infrastructure.persistence.feature_flags import DurableFeatureFlagRegistry
    durable_flags = DurableFeatureFlagRegistry(store)
    durable_flags.initialize_defaults()
    # The identity schema is PostgreSQL-only (migration 000011), so SQLite mode
    # carries in-memory doubles rather than None: the auth boundary always has
    # an identity store and a session verifier and never silently skips them.
    from shared.identity import (
        InMemoryIdentityStore,
        InMemorySessionRepository,
        SessionConfig,
        SessionService,
    )

    durable_identity_store = InMemoryIdentityStore()
    durable_session_service = SessionService(
        repository=InMemorySessionRepository(),
        config=SessionConfig(),
    )

    worm_root = Path(db_path).parent / f"{Path(db_path).stem}-audit-worm"
    resolved_worm_sink = worm_sink or build_audit_worm_sink_from_env(
        default_root=worm_root
    )
    durable_audit_log = DurableAuditLog(engine, worm_sink=resolved_worm_sink)
    durable_manual_corr_repo = DurableManualCorrectionRepository(engine)

    return PersistenceBundle(
        mode="durable",
        audit_log=durable_audit_log,
        evidence_store=DurableEvidenceBundleStore(engine, worm_sink=resolved_worm_sink),
        job_queue=DurableJobQueue(engine, flags=durable_flags),
        feature_flag_registry=durable_flags,
        avm_repository=DurableAVMRepository(store),
        forecastops_repository=DurableForecastOpsRepository(store),
        forecastops_policy_repository=_default_decision_policy_repository(),
        netplan_repository=DurableNetPlanRepository(store),
        learninghub_repository=DurableLearningHubRepository(store),
        artifact_store=DurableArtifactStore(store),
        priceops_repository=DurablePriceOpsRepository(store),
        sitescore_repository=DurableSiteScoreRepository(store),
        adlift_repository=DurableAdLiftRepository(store),
        adlift_campaign_repository=DurableAdLiftCampaignRepository(store),
        survey_assignment_repository=DurableSurveyAssignmentRepository(store),
        store_ops_repository=DurableStoreOpsRepository(store),
        intervention_repository=DurableInterventionRepository(store),
        intervention_label_registry=DurableLabelRegistry(store),
        ingestion_run_store=DurableIngestionRunStore(store),
        heatzone_store=DurableHeatZoneResultStore(store),
        heatzone_composition_repository=DurableHeatZoneCompositionRepository(engine),
        heatzone_evidence_repository=DurableMergeSplitEvidenceRepository(engine),
        heatzone_absorption_outcome_writer=DurableAbsorptionOutcomeWriter(engine),
        listing_repository=DurableListingRepository(store),
        sitescore_decision_store=DurableDecisionStore(store),
        sitescore_realized_store=DurableRealizedSiteStore(store),
        tenant_repository=DurableTenantRepository(engine),
        brand_repository=DurableBrandRepository(engine),
        address_location_repository=DurableAddressLocationRepository(
            engine,
            correction_repo=durable_manual_corr_repo,
            audit_log=durable_audit_log,
        ),
        manual_correction_repository=durable_manual_corr_repo,
        store_repository=DurableStoreRepository(engine),
        machine_repository=DurableMachineRepository(engine),
        transaction_repository=DurableTransactionRepository(engine),
        machine_cycle_repository=DurableMachineCycleRepository(engine),
        external_fetch_state_store=DurableExternalFetchStateStore(store),
        notification_repository=DurableNotificationRepository(engine),
        outbox_repository=DurableOutboxRepository(engine),
        operator_intake_repository=DurableAssistedIntakeRepository(store),
        engine=engine,
        identity_store=durable_identity_store,
        session_service=durable_session_service,
    )


def _postgres_bundle(
    database_url: str,
    *,
    worm_sink: AuditWormSink | None = None,
) -> PersistenceBundle:
    from modules.adlift.infrastructure.campaign_repository import DurableAdLiftCampaignRepository
    from modules.external_data.workers.scheduled_fetch import DurableExternalFetchStateStore
    from modules.market_survey.infrastructure.assignment_repository import (
        DurableSurveyAssignmentRepository,
    )
    from modules.notifications import DurableNotificationRepository
    from modules.opsboard.application.store_ops import DurableStoreOpsRepository
    from modules.opsboard.audit.evidence_store import DurableEvidenceBundleStore
    from shared.infrastructure.persistence.assisted_listing_intake import (
        DurableAssistedIntakeStore,
        validate_required_tables,
    )
    from shared.infrastructure.persistence.audit_log import DurableAuditLog
    from shared.infrastructure.persistence.decision_policy import SqlDecisionPolicyRepository
    from shared.infrastructure.persistence.external_data import DurableIngestionRunStore
    from shared.infrastructure.persistence.job_queue import DurableJobQueue
    from shared.infrastructure.persistence.operator_network_listings import (
        DurableAssistedIntakeRepository,
    )
    from shared.infrastructure.persistence.outbox import DurableOutboxRepository
    from shared.infrastructure.persistence.postgresql import (
        PostgresDocumentStore,
        PostgresEngine,
    )
    from shared.infrastructure.persistence.repositories import (
        DurableAbsorptionOutcomeWriter,
        DurableAddressLocationRepository,
        DurableAdLiftRepository,
        DurableArtifactStore,
        DurableAVMRepository,
        DurableBrandRepository,
        DurableDecisionStore,
        DurableForecastOpsRepository,
        DurableHeatZoneCompositionRepository,
        DurableHeatZoneResultStore,
        DurableInterventionRepository,
        DurableLabelRegistry,
        DurableLearningHubRepository,
        DurableListingRepository,
        DurableMachineCycleRepository,
        DurableMachineRepository,
        DurableManualCorrectionRepository,
        DurableMergeSplitEvidenceRepository,
        DurableNetPlanRepository,
        DurablePriceOpsRepository,
        DurableRealizedSiteStore,
        DurableSiteScoreRepository,
        DurableStoreRepository,
        DurableTenantRepository,
        DurableTransactionRepository,
    )

    engine = PostgresEngine(database_url)
    store = PostgresDocumentStore(engine)
    try:
        validate_required_tables(engine)
        assisted_intake_store = DurableAssistedIntakeStore(store)
    except Exception:
        engine.close()
        raise

    # Wire durable identity and session stores backed by the identity.* tables.
    # Both must be durable: an in-memory session repository would make
    # revocations process-local, so a revoked sid stays valid on every other
    # instance (Contract §5.1, §5.4).
    from shared.identity import (
        SessionConfig,
        SessionService,
        SqlIdentityStore,
        SqlSessionRepository,
    )

    # engine.pooled_connection is a context manager factory, so each query
    # borrows a pooled connection and returns it on exit.
    pg_identity_store = SqlIdentityStore(connection_factory=engine.pooled_connection)
    pg_session_service = SessionService(
        repository=SqlSessionRepository(connection_factory=engine.pooled_connection),
        config=SessionConfig(),
    )

    from modules.listing.application.intake_registry_calendar import RegistryIntakeWorkflowPolicy

    pg_intake_workflow_policy = RegistryIntakeWorkflowPolicy(
        SqlDecisionPolicyRepository(engine), pg_identity_store
    )

    from modules.listing.application.intake_audit import (
        CanonicalIntakeAuditSink,
        RegistryIntakeAuditRetention,
    )
    from shared.audit.canonical_delivery import CanonicalAuditDelivery
    from shared.audit.canonical_postgresql import CanonicalPostgresAuditLog

    resolved_worm_sink = worm_sink or build_audit_worm_sink_from_env()
    from shared.infrastructure.persistence.feature_flags import DurableFeatureFlagRegistry
    pg_flags = DurableFeatureFlagRegistry(store)
    pg_flags.initialize_defaults()
    pg_job_queue = DurableJobQueue(engine, canonical_intake_score_schedule=True, flags=pg_flags)
    canonical_audit_writer = CanonicalPostgresAuditLog(engine)

    assisted_intake_store.configure_canonical_audit(CanonicalIntakeAuditSink(
        canonical_audit_writer,
        RegistryIntakeAuditRetention(SqlDecisionPolicyRepository(engine), pg_identity_store),
        CanonicalAuditDelivery(canonical_audit_writer, pg_job_queue, resolved_worm_sink),
    ))

    pg_audit_log = DurableAuditLog(engine, worm_sink=resolved_worm_sink)
    pg_manual_corr_repo = DurableManualCorrectionRepository(engine)
    from shared.infrastructure.persistence.price_exploration import CanonicalPriceExplorationStore

    return PersistenceBundle(
        mode="postgresql",
        audit_log=pg_audit_log,
        evidence_store=DurableEvidenceBundleStore(
            engine,
            worm_sink=resolved_worm_sink,
        ),
        job_queue=pg_job_queue,
        feature_flag_registry=pg_flags,
        avm_repository=DurableAVMRepository(store),
        forecastops_repository=DurableForecastOpsRepository(store),
        forecastops_policy_repository=SqlDecisionPolicyRepository(engine),
        netplan_repository=DurableNetPlanRepository(store),
        learninghub_repository=DurableLearningHubRepository(store),
        artifact_store=DurableArtifactStore(store),
        priceops_repository=DurablePriceOpsRepository(
            store, canonical_exploration=CanonicalPriceExplorationStore(engine),
        ),
        sitescore_repository=DurableSiteScoreRepository(store),
        adlift_repository=DurableAdLiftRepository(store),
        adlift_campaign_repository=DurableAdLiftCampaignRepository(store),
        survey_assignment_repository=DurableSurveyAssignmentRepository(store),
        store_ops_repository=DurableStoreOpsRepository(store),
        intervention_repository=DurableInterventionRepository(store),
        intervention_label_registry=DurableLabelRegistry(store),
        ingestion_run_store=DurableIngestionRunStore(store),
        heatzone_store=DurableHeatZoneResultStore(store),
        heatzone_composition_repository=DurableHeatZoneCompositionRepository(engine),
        heatzone_evidence_repository=DurableMergeSplitEvidenceRepository(engine),
        heatzone_absorption_outcome_writer=DurableAbsorptionOutcomeWriter(engine),
        listing_repository=DurableListingRepository(store),
        sitescore_decision_store=DurableDecisionStore(store),
        sitescore_realized_store=DurableRealizedSiteStore(store),
        tenant_repository=DurableTenantRepository(engine),
        brand_repository=DurableBrandRepository(engine),
        address_location_repository=DurableAddressLocationRepository(
            engine,
            correction_repo=pg_manual_corr_repo,
            audit_log=pg_audit_log,
        ),
        manual_correction_repository=pg_manual_corr_repo,
        store_repository=DurableStoreRepository(engine),
        machine_repository=DurableMachineRepository(engine),
        transaction_repository=DurableTransactionRepository(engine),
        machine_cycle_repository=DurableMachineCycleRepository(engine),
        external_fetch_state_store=DurableExternalFetchStateStore(store),
        notification_repository=DurableNotificationRepository(engine),
        outbox_repository=DurableOutboxRepository(engine),
        operator_intake_repository=DurableAssistedIntakeRepository(store),
        assisted_intake_store=assisted_intake_store,
        assisted_intake_workflow_policy=pg_intake_workflow_policy,
        engine=engine,
        identity_store=pg_identity_store,
        session_service=pg_session_service,
    )


def build_persistence(
    *,
    mode: str | None = None,
    db_path: str | Path | None = None,
) -> PersistenceBundle:
    """Build the persistence bundle for the configured backend.

    Args mirror the env knobs and override them when supplied (used by tests).
    """
    resolved_mode = (mode or os.environ.get("ODP_PERSISTENCE", "memory")).strip().lower()
    require_live_data = (
        os.environ.get("ODP_REQUIRE_LIVE_DATA", "").strip().lower()
        in _TRUTHY_ENV_VALUES
    )
    if resolved_mode not in _SUPPORTED_MODES:
        raise ValueError(
            "Unsupported ODP_PERSISTENCE mode "
            f"{resolved_mode!r}; supported modes are memory, durable, sqlite, "
            "postgres, and postgresql."
        )
    if require_live_data and resolved_mode not in _POSTGRES_MODES:
        raise RuntimeError(
            "ODP_REQUIRE_LIVE_DATA=true requires a production PostgreSQL "
            "persistence bundle; memory and SQLite are local/test surrogates."
        )
    worm_sink = build_audit_worm_sink_from_env()
    if resolved_mode in _POSTGRES_MODES:
        if db_path is not None:
            raise ValueError("db_path is only valid for SQLite persistence")
        resolved_url = os.environ.get("ODAY_DATABASE_URL", "").strip()
        if not resolved_url:
            raise RuntimeError(
                "ODAY_DATABASE_URL is required for PostgreSQL persistence"
            )
        bundle = _postgres_bundle(resolved_url, worm_sink=worm_sink)
        if require_live_data and not bundle.is_production:
            bundle.engine.close()
            raise RuntimeError(
                "ODP_REQUIRE_LIVE_DATA=true requires a verified production "
                "PostgreSQL persistence bundle"
            )
        return bundle
    if resolved_mode in _DURABLE_MODES:
        resolved_path = db_path or os.environ.get("ODP_DB_PATH", DEFAULT_DB_PATH)
        return _durable_bundle(resolved_path, worm_sink=worm_sink)
    return _memory_bundle(worm_sink=worm_sink)


__all__ = ["DEFAULT_DB_PATH", "PersistenceBundle", "build_persistence"]

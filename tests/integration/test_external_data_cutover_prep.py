"""Reversible consumer cutover activation (ODP-XR-CUTOVER-ACTIVATE-002).

ODayPlus is updated to read the versioned data-platform snapshot by default
(PLATFORM_PRIMARY), with external acquisition remaining disabled by default,
while the reversible switch and kill switch allow emergency rollback.
"""

from __future__ import annotations

import pytest

from apps.scheduler.oday_scheduler.main import (
    EXTERNAL_FETCH_JOB_TYPE,
    ODayScheduler,
    SchedulerCutoverConfigurationError,
    SchedulerTenantConfigurationError,
)
from delivery_toolchain.e2e import seed_product_e2e_data
from modules.external_data.application import market_data_facade as facade_module
from modules.external_data.application.market_data_facade import (
    CUTOVER_MODE_DUAL_RUN,
    CUTOVER_MODE_LEGACY_ONLY,
    CUTOVER_MODE_PLATFORM_PRIMARY,
    CUTOVER_MODES,
    DEFAULT_CUTOVER_MODE,
    FACADE_MODE_ENV,
    KILL_SWITCH_ENV,
    MarketDataFacade,
    MarketDataValidationError,
    cutover_state,
    kill_switch_active,
    legacy_external_fetch_enabled,
    platform_read_enabled,
    resolve_cutover_mode,
    rollback_probe,
)
from shared.infrastructure.persistence.factory import build_persistence
from shared.jobs.queue import JobRecord, NonRetryableJobError

LEGACY_ENV = {FACADE_MODE_ENV: CUTOVER_MODE_LEGACY_ONLY}
DUAL_RUN_ENV = {FACADE_MODE_ENV: CUTOVER_MODE_DUAL_RUN}
PLATFORM_ENV = {FACADE_MODE_ENV: CUTOVER_MODE_PLATFORM_PRIMARY}
ROLLED_BACK_ENV = {
    FACADE_MODE_ENV: CUTOVER_MODE_PLATFORM_PRIMARY,
    KILL_SWITCH_ENV: "true",
}


# ---------------------------------------------------------------------------
# The switch itself
# ---------------------------------------------------------------------------


def test_unconfigured_deployment_reads_the_platform_by_default() -> None:
    """ODP-XR-CUTOVER-ACTIVATE-002: platform snapshot is now the default read path."""
    assert DEFAULT_CUTOVER_MODE == CUTOVER_MODE_PLATFORM_PRIMARY
    assert resolve_cutover_mode({}) == CUTOVER_MODE_PLATFORM_PRIMARY
    assert legacy_external_fetch_enabled({}) is False
    assert platform_read_enabled({}) is True


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("LEGACY_ONLY", CUTOVER_MODE_LEGACY_ONLY),
        ("DUAL_RUN", CUTOVER_MODE_DUAL_RUN),
        ("PLATFORM_PRIMARY", CUTOVER_MODE_PLATFORM_PRIMARY),
        ("platform_primary", CUTOVER_MODE_PLATFORM_PRIMARY),
        ("  DUAL_RUN  ", CUTOVER_MODE_DUAL_RUN),
        # PR #970's vocabulary for the rolled-back state resolves rather than
        # becoming a fourth mode indistinguishable from LEGACY_ONLY.
        ("LEGACY_FALLBACK", CUTOVER_MODE_LEGACY_ONLY),
    ],
)
def test_configured_mode_resolves(configured: str, expected: str) -> None:
    assert resolve_cutover_mode({FACADE_MODE_ENV: configured}) == expected


def test_dual_run_keeps_fetching_while_reading_the_platform() -> None:
    """Dual run is the comparison state: both arms live, neither retired."""
    assert legacy_external_fetch_enabled(DUAL_RUN_ENV) is True
    assert platform_read_enabled(DUAL_RUN_ENV) is True


def test_platform_primary_retires_fetch_and_serves_the_platform() -> None:
    assert legacy_external_fetch_enabled(PLATFORM_ENV) is False
    assert platform_read_enabled(PLATFORM_ENV) is True


@pytest.mark.parametrize("truthy", ["1", "true", "TRUE", "yes", "on"])
def test_kill_switch_rolls_a_cut_over_deployment_back(truthy: str) -> None:
    env = {FACADE_MODE_ENV: CUTOVER_MODE_PLATFORM_PRIMARY, KILL_SWITCH_ENV: truthy}
    assert kill_switch_active(env) is True
    assert resolve_cutover_mode(env) == CUTOVER_MODE_LEGACY_ONLY
    assert legacy_external_fetch_enabled(env) is True


def test_kill_switch_is_not_blocked_by_a_typo_in_the_mode_it_rolls_back_from() -> None:
    """The emergency lever must win before the value it overrides is validated.

    An operator rolling back at 3am should not be stopped because the variable
    they are rolling back *from* is the one that was mistyped.
    """
    env = {FACADE_MODE_ENV: "PLATFORM_PRIMAR", KILL_SWITCH_ENV: "true"}
    assert resolve_cutover_mode(env) == CUTOVER_MODE_LEGACY_ONLY


def test_unknown_mode_is_refused_rather_than_guessed() -> None:
    with pytest.raises(MarketDataValidationError) as excinfo:
        resolve_cutover_mode({FACADE_MODE_ENV: "PLATFORM_PRIMAR"})

    assert excinfo.value.details["env_var"] == FACADE_MODE_ENV
    assert excinfo.value.details["value"] == "PLATFORM_PRIMAR"
    assert excinfo.value.details["supported"] == list(CUTOVER_MODES)


def test_cutover_state_separates_configured_from_effective() -> None:
    """A pulled kill switch must read as a rollback, not as an edited config."""
    state = cutover_state(ROLLED_BACK_ENV)

    assert state["configured_mode"] == CUTOVER_MODE_PLATFORM_PRIMARY
    assert state["mode"] == CUTOVER_MODE_LEGACY_ONLY
    assert state["kill_switch_active"] is True
    assert state["legacy_external_fetch_enabled"] is True
    assert state["platform_read_enabled"] is False


# ---------------------------------------------------------------------------
# The data-platform snapshot read path
# ---------------------------------------------------------------------------


class _StubClient:
    """Minimal read-only client double; records every call it receives."""

    def __init__(self, integrity: dict | Exception) -> None:
        self._integrity = integrity
        self.calls: list[str] = []

    def verify_integrity(self) -> dict:
        self.calls.append("verify_integrity")
        if isinstance(self._integrity, Exception):
            raise self._integrity
        return self._integrity


HEALTHY_INTEGRITY = {
    "status": "healthy",
    "foundation": {
        "compatible": True,
        "release_id": "foundation-release-1",
        "semantic_version": "0.4.1",
        "contracts_checked": 7,
    },
    "product": {
        "compatible": False,
        "release_id": "product-release-1",
        "semantic_version": "0.4.1",
        "contracts_checked": 4,
    },
}


def test_snapshot_read_path_reports_published_release_provenance() -> None:
    client = _StubClient(HEALTHY_INTEGRITY)
    facade = MarketDataFacade(client=client, enforce_auth=False)

    snapshot = facade.get_platform_snapshot(correlation_id="corr-1", env=PLATFORM_ENV)

    assert snapshot["source"] == "data_platform"
    assert snapshot["status"] == "healthy"
    assert snapshot["mode"] == CUTOVER_MODE_PLATFORM_PRIMARY
    # Asserted in the payload a verifier reads back, not only in a docstring.
    assert snapshot["writes"] == 0
    assert client.calls == ["verify_integrity"]

    rows = {row["provider_id"]: row for row in snapshot["freshness"]}
    assert set(rows) == {"data_platform.foundation", "data_platform.product"}
    assert rows["data_platform.foundation"]["source_snapshot_id"] == "foundation-release-1"
    assert rows["data_platform.foundation"]["data_status"] == "FRESH"
    assert rows["data_platform.foundation"]["quality_flags"] == []
    # An incompatible arm is reported as stale rather than quietly as fresh.
    assert rows["data_platform.product"]["data_status"] == "STALE"
    assert rows["data_platform.product"]["quality_flags"] == ["release_incompatible"]
    # These are timestamps of a fetch odayplus performed; after the cutover it
    # performs none, so they stay absent instead of being invented.
    assert rows["data_platform.product"]["provider_observed_at"] is None
    assert rows["data_platform.product"]["ingested_at"] is None
    assert all(row["correlation_id"] == "corr-1" for row in snapshot["freshness"])


def test_snapshot_read_path_emits_no_row_for_an_unverifiable_arm() -> None:
    """A row for an arm with no release id would report the cutover as healthier
    than it is."""
    client = _StubClient(
        {
            "foundation": {"compatible": True, "release_id": "foundation-release-1"},
            "product": {"compatible": True, "release_id": ""},
        }
    )
    facade = MarketDataFacade(client=client, enforce_auth=False)

    snapshot = facade.get_platform_snapshot(env=PLATFORM_ENV)

    assert [row["provider_id"] for row in snapshot["freshness"]] == [
        "data_platform.foundation"
    ]


def test_snapshot_read_path_degrades_instead_of_raising() -> None:
    from modules.external_data.infrastructure.data_platform_client import (
        DataPlatformIntegrityError,
    )

    client = _StubClient(DataPlatformIntegrityError("release mismatch"))
    facade = MarketDataFacade(client=client, enforce_auth=False)

    snapshot = facade.get_platform_snapshot(env=PLATFORM_ENV)

    assert snapshot["status"] == "degraded"
    assert snapshot["freshness"] == []
    assert "release mismatch" in snapshot["error"]
    assert snapshot["writes"] == 0


def test_snapshot_read_path_reaches_the_real_pinned_release() -> None:
    """Not a stub: the generated clients must resolve an actual released id."""
    from modules.external_data.infrastructure.data_platform_client import (
        DataPlatformClient,
        InMemoryDataPlatformTransport,
    )

    facade = MarketDataFacade(
        client=DataPlatformClient(transport=InMemoryDataPlatformTransport()),
        enforce_auth=False,
    )

    snapshot = facade.get_platform_snapshot(correlation_id="corr-real", env=PLATFORM_ENV)

    assert snapshot["status"] == "healthy"
    assert {row["provider_id"] for row in snapshot["freshness"]} == {
        "data_platform.foundation",
        "data_platform.product",
    }
    assert all(row["source_snapshot_id"] for row in snapshot["freshness"])


# ---------------------------------------------------------------------------
# The rollback probe consumed by the producer-side cutover verifier
# ---------------------------------------------------------------------------


def test_rollback_probe_reads_the_platform_by_default() -> None:
    probe = rollback_probe({})

    assert probe["mode"] == CUTOVER_MODE_PLATFORM_PRIMARY
    assert probe["source"] == "platform"
    assert probe["payload"]["contract"] == "emgi.site-market-context.v1"
    assert probe["writes"] == 0


def test_rollback_probe_reads_legacy_when_configured() -> None:
    probe = rollback_probe(LEGACY_ENV)

    assert probe["mode"] == CUTOVER_MODE_LEGACY_ONLY
    assert probe["source"] == "legacy"
    assert probe["writes"] == 0
    assert probe["payload"]["site_id"] == facade_module.ROLLBACK_PROBE_SITE_ID


def test_rollback_probe_payload_is_stable_across_repeated_reads() -> None:
    """The verifier detects corruption by comparing repeated rollback reads."""
    assert rollback_probe({}) == rollback_probe({})


def test_rollback_probe_reads_the_platform_when_cut_over() -> None:
    probe = rollback_probe(PLATFORM_ENV)

    assert probe["mode"] == CUTOVER_MODE_PLATFORM_PRIMARY
    assert probe["source"] == "platform"
    assert probe["payload"]["contract"] == "emgi.site-market-context.v1"
    assert probe["writes"] == 0


def test_rollback_probe_returns_both_arms_in_dual_run() -> None:
    probe = rollback_probe(DUAL_RUN_ENV)

    assert probe["mode"] == CUTOVER_MODE_DUAL_RUN
    # Neither arm is authoritative yet; that is what dual run is for.
    assert probe["payload"]["source"] == "legacy"
    assert probe["platform_payload"]["source"] == "platform"
    assert probe["snapshot"]["writes"] == 0
    assert probe["writes"] == 0


def test_rollback_probe_follows_the_kill_switch() -> None:
    assert rollback_probe(ROLLED_BACK_ENV)["source"] == "legacy"


# ---------------------------------------------------------------------------
# API: the manual ingestion trigger
# ---------------------------------------------------------------------------


@pytest.fixture
def api_client(monkeypatch):
    """A TestClient factory that applies cutover env before the app is built."""
    from fastapi.testclient import TestClient

    from apps.api.oday_api.main import create_app

    def build(env: dict[str, str]):
        monkeypatch.setenv("ODP_PRODUCT_MODE", "poc")
        for name in (FACADE_MODE_ENV, KILL_SWITCH_ENV):
            monkeypatch.delenv(name, raising=False)
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        return TestClient(create_app())

    return build


API_HEADERS = {
    "x-subject-id": "cutover-test-operator",
    "x-roles": "data_owner",
    "x-tenant-id": "tenant-cutover",
    "x-correlation-id": "corr-cutover",
}


def _trigger(client):
    return client.post(
        "/external-data/ingestion-runs",
        headers=API_HEADERS,
        json={"provider_id": "listing.partner_feed", "schedule_id": "manual"},
    )


@pytest.mark.parametrize("env", [LEGACY_ENV, DUAL_RUN_ENV], ids=["legacy", "dual_run"])
def test_manual_trigger_keeps_working_when_configured(
    api_client, env: dict[str, str]
) -> None:
    response = _trigger(api_client(env))

    assert response.status_code == 202


def test_manual_trigger_answers_410_with_a_branchable_code_by_default(
    api_client,
) -> None:
    response = _trigger(api_client({}))

    assert response.status_code == 410
    error = response.json()["error"]
    assert error["code"] == "external_fetch_decommissioned"
    assert "next_action" in error


def test_manual_trigger_returns_on_rollback_without_a_redeploy(api_client) -> None:
    """The reversibility claim, proved against one running app instance."""
    client = api_client(PLATFORM_ENV)
    assert _trigger(client).status_code == 410

    import os

    os.environ[KILL_SWITCH_ENV] = "true"
    try:
        assert _trigger(client).status_code == 202
    finally:
        os.environ.pop(KILL_SWITCH_ENV, None)


def test_manual_trigger_fails_loudly_on_an_unreadable_mode(api_client) -> None:
    response = _trigger(api_client({FACADE_MODE_ENV: "PLATFORM_PRIMAR"}))

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "external_data_cutover_mode_invalid"


# ---------------------------------------------------------------------------
# API: freshness
# ---------------------------------------------------------------------------


def _freshness(client):
    return client.get("/external-data/freshness", headers=API_HEADERS)


def test_freshness_is_served_from_the_platform_by_default(api_client) -> None:
    response = _freshness(api_client({}))

    assert response.status_code == 200
    body = response.json()
    assert body["availability"]["source"] == "data_platform"
    assert body["correlation_id"] == "corr-cutover"
    assert {row["provider_id"] for row in body["freshness"]} == {
        "data_platform.foundation",
        "data_platform.product",
    }
    assert all(row["provider_id"].startswith("data_platform.") for row in body["freshness"])


def test_freshness_serves_legacy_when_configured(api_client) -> None:
    response = _freshness(api_client(LEGACY_ENV))

    assert response.status_code == 200
    body = response.json()
    assert body["availability"]["source"] == "fixture"
    assert body["freshness"][0]["provider_id"] == "listing.partner_feed"
    # The switch is observable, but it does not add a second opinion here.
    assert body["cutover"]["mode"] == CUTOVER_MODE_LEGACY_ONLY
    assert "dual_run" not in body


def test_freshness_dual_run_compares_without_changing_the_authoritative_answer(
    api_client,
) -> None:
    response = _freshness(api_client(DUAL_RUN_ENV))

    assert response.status_code == 200
    body = response.json()
    # Legacy stays authoritative: dual run observes, it does not switch.
    assert body["availability"]["source"] == "fixture"
    assert body["freshness"][0]["provider_id"] == "listing.partner_feed"

    platform_arm = body["dual_run"]
    assert platform_arm["availability"]["source"] == "data_platform"
    assert platform_arm["availability"]["status"] == "AVAILABLE"
    assert {row["provider_id"] for row in platform_arm["freshness"]} == {
        "data_platform.foundation",
        "data_platform.product",
    }


def test_freshness_reports_an_unwired_platform_arm_instead_of_inventing_one(
    api_client, monkeypatch
) -> None:
    """A deployment with no bound transport must say so, not serve an empty read.

    `main.py` leaves the facade `None` when no production binding resolves, so
    the route has to report an unwired arm rather than create an implicit empty
    transport of its own.
    """
    # `main.py` imports the factory inside its composition function, so the
    # route module is where the binding has to be replaced.
    import apps.api.app.routes.external_data as routes

    real = routes.create_external_data_router

    def unwired(**kwargs):
        kwargs["market_data_facade"] = None
        return real(**kwargs)

    monkeypatch.setattr(routes, "create_external_data_router", unwired)

    body = _freshness(api_client(PLATFORM_ENV)).json()

    assert body["freshness"] == []
    assert body["availability"] == {
        "status": "UNAVAILABLE",
        "reason_code": "PLATFORM_FACADE_NOT_WIRED",
        "source": "data_platform",
    }


def test_freshness_dual_run_survives_a_failing_platform_arm(
    api_client, monkeypatch
) -> None:
    """In dual run the legacy answer is authoritative, so a platform-side
    failure is reported as an unavailable arm rather than taken out on the caller."""

    class _Broken:
        def get_platform_snapshot(self, **_kwargs):
            raise RuntimeError("platform read exploded")

    import apps.api.app.routes.external_data as routes

    real = routes.create_external_data_router

    def broken(**kwargs):
        kwargs["market_data_facade"] = _Broken()
        return real(**kwargs)

    monkeypatch.setattr(routes, "create_external_data_router", broken)

    response = _freshness(api_client(DUAL_RUN_ENV))

    assert response.status_code == 200
    body = response.json()
    assert body["availability"]["source"] == "fixture"
    assert body["dual_run"]["availability"]["reason_code"] == "PLATFORM_SNAPSHOT_READ_FAILED"
    assert "platform read exploded" in body["dual_run"]["error"]


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------


def _scheduler(env: dict[str, str], *, tenant_id: str = "tenant-cutover"):
    return ODayScheduler(
        persistence=build_persistence(), tenant_id=tenant_id, env=env
    )


@pytest.mark.parametrize("env", [LEGACY_ENV, DUAL_RUN_ENV], ids=["legacy", "dual_run"])
def test_scheduler_enqueues_when_configured(
    env: dict[str, str],
) -> None:
    scheduler = _scheduler(env)

    assert scheduler.recurring_job_types() == (EXTERNAL_FETCH_JOB_TYPE,)


def test_scheduler_enqueues_nothing_by_default() -> None:
    scheduler = _scheduler({})
    enqueued: list = []
    scheduler.job_queue.enqueue = lambda *a, **kw: enqueued.append((a, kw))

    scheduler.run_once()

    assert scheduler.recurring_job_types() == ()
    assert enqueued == []


def test_scheduler_still_enqueues_when_configured_legacy() -> None:
    scheduler = _scheduler(LEGACY_ENV)
    enqueued: list = []
    scheduler.job_queue.enqueue = lambda request, **kw: enqueued.append(request)

    scheduler.run_once()

    assert [request.job_type for request in enqueued] == [EXTERNAL_FETCH_JOB_TYPE]


def test_scheduler_resumes_enqueueing_when_the_kill_switch_is_pulled() -> None:
    assert _scheduler(ROLLED_BACK_ENV).recurring_job_types() == (EXTERNAL_FETCH_JOB_TYPE,)


def test_scheduler_still_reports_a_missing_tenant_after_cutover() -> None:
    """A deployment that lost its tenant is misconfigured whether or not there
    is work to enqueue, so the guard must not be skipped by the cutover."""
    scheduler = _scheduler(PLATFORM_ENV, tenant_id="")

    with pytest.raises(SchedulerTenantConfigurationError):
        scheduler.run_once()


def test_scheduler_refuses_an_unreadable_mode_rather_than_guessing() -> None:
    scheduler = _scheduler({FACADE_MODE_ENV: "BOGUS"})
    enqueued: list = []
    scheduler.job_queue.enqueue = lambda *a, **kw: enqueued.append((a, kw))

    with pytest.raises(SchedulerCutoverConfigurationError) as excinfo:
        scheduler.run_once()

    assert excinfo.value.code == "external_data_cutover_mode_invalid"
    assert enqueued == []


def test_scheduler_loop_survives_an_unreadable_mode() -> None:
    """Fail closed and stay loud: a bad mode must not become a restart loop."""

    class _Stop:
        def __init__(self) -> None:
            self.ticks = 0

        def is_set(self) -> bool:
            self.ticks += 1
            return self.ticks > 1

    scheduler = _scheduler({FACADE_MODE_ENV: "BOGUS"})
    scheduler.export_metrics = lambda: None

    scheduler.loop(stop_event=_Stop(), interval=0)


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------


def _external_fetch_job() -> JobRecord:
    from apps.worker.oday_worker import handlers

    return JobRecord(
        job_type=handlers.EXTERNAL_FETCH_JOB_TYPE,
        payload={
            "tenant_id": "tenant-cutover",
            "provider_id": "listing.partner_feed",
            "schedule_id": "hourly-listing",
        },
        correlation_id="corr-cutover",
    )


class _ExplodingPersistence:
    """Any attribute read means the handler started doing ingestion work."""

    def __getattr__(self, name: str):
        raise AssertionError(f"cutover handler must not touch persistence.{name}")


def test_worker_dead_letters_external_fetch_by_default(monkeypatch) -> None:
    from apps.worker.oday_worker import handlers

    monkeypatch.delenv(FACADE_MODE_ENV, raising=False)
    monkeypatch.delenv(KILL_SWITCH_ENV, raising=False)

    with pytest.raises(NonRetryableJobError) as excinfo:
        handlers.handle_external_fetch(_external_fetch_job(), _ExplodingPersistence())

    message = str(excinfo.value)
    assert "decommissioned" in message
    assert CUTOVER_MODE_PLATFORM_PRIMARY in message


def test_worker_dead_letters_rather_than_retrying_an_unreadable_mode(monkeypatch) -> None:
    from apps.worker.oday_worker import handlers

    monkeypatch.setenv(FACADE_MODE_ENV, "BOGUS")
    monkeypatch.delenv(KILL_SWITCH_ENV, raising=False)

    with pytest.raises(NonRetryableJobError) as excinfo:
        handlers.handle_external_fetch(_external_fetch_job(), _ExplodingPersistence())

    assert FACADE_MODE_ENV in str(excinfo.value)


def test_external_fetch_stays_registered_after_cutover(monkeypatch) -> None:
    """An unregistered type would retry to an opaque 'unknown job type' instead
    of dead-lettering on the first attempt with the operator's reason."""
    from apps.worker.oday_worker import handlers

    monkeypatch.setenv(FACADE_MODE_ENV, CUTOVER_MODE_PLATFORM_PRIMARY)

    registry = handlers.build_default_registry()

    assert registry.get(handlers.EXTERNAL_FETCH_JOB_TYPE) is handlers.handle_external_fetch


def test_worker_runs_the_legacy_path_when_configured(monkeypatch) -> None:
    """Explicit LEGACY_ONLY mode allows the handler to proceed past the switch."""
    from apps.worker.oday_worker import handlers

    monkeypatch.setenv(FACADE_MODE_ENV, CUTOVER_MODE_LEGACY_ONLY)
    monkeypatch.delenv(KILL_SWITCH_ENV, raising=False)

    with pytest.raises(AssertionError) as excinfo:
        handlers.handle_external_fetch(_external_fetch_job(), _ExplodingPersistence())

    assert "must not touch persistence" in str(excinfo.value)


def test_worker_resumes_the_legacy_path_when_the_kill_switch_is_pulled(
    monkeypatch,
) -> None:
    from apps.worker.oday_worker import handlers

    monkeypatch.setenv(FACADE_MODE_ENV, CUTOVER_MODE_PLATFORM_PRIMARY)
    monkeypatch.setenv(KILL_SWITCH_ENV, "true")

    with pytest.raises(AssertionError) as excinfo:
        handlers.handle_external_fetch(_external_fetch_job(), _ExplodingPersistence())

    assert "must not touch persistence" in str(excinfo.value)


# ---------------------------------------------------------------------------
# E2E seeding
# ---------------------------------------------------------------------------


def test_seed_vocabulary_cannot_drift_from_the_facade() -> None:
    """The seed script restates the contract because it runs without the repo on
    sys.path. This is the assertion that keeps the restatement honest."""
    assert seed_product_e2e_data.FACADE_MODE_ENV == FACADE_MODE_ENV
    assert seed_product_e2e_data.KILL_SWITCH_ENV == KILL_SWITCH_ENV
    assert seed_product_e2e_data.DEFAULT_CUTOVER_MODE == DEFAULT_CUTOVER_MODE

    for mode in [*seed_product_e2e_data.LEGACY_FETCH_MODES, *CUTOVER_MODES]:
        env = {FACADE_MODE_ENV: mode}
        assert seed_product_e2e_data.legacy_ingestion_trigger_available(
            env
        ) is legacy_external_fetch_enabled(env), mode

    for truthy in seed_product_e2e_data.KILL_SWITCH_TRUTHY:
        env = {FACADE_MODE_ENV: CUTOVER_MODE_PLATFORM_PRIMARY, KILL_SWITCH_ENV: truthy}
        assert seed_product_e2e_data.legacy_ingestion_trigger_available(env) is True
        assert kill_switch_active(env) is True


def test_seed_does_not_call_the_retired_trigger_by_default(monkeypatch) -> None:
    monkeypatch.delenv(FACADE_MODE_ENV, raising=False)
    monkeypatch.delenv(KILL_SWITCH_ENV, raising=False)

    posted: list = []
    monkeypatch.setattr(
        seed_product_e2e_data,
        "post_json",
        lambda url, payload: posted.append(url),
    )

    assert seed_product_e2e_data.legacy_ingestion_trigger_available() is False
    assert posted == []


def test_seed_still_triggers_ingestion_when_configured(monkeypatch) -> None:
    monkeypatch.setenv(FACADE_MODE_ENV, CUTOVER_MODE_LEGACY_ONLY)
    monkeypatch.delenv(KILL_SWITCH_ENV, raising=False)

    assert seed_product_e2e_data.legacy_ingestion_trigger_available() is True


def test_seed_waits_for_a_real_published_platform_snapshot(monkeypatch) -> None:
    empty = {"availability": {"source": "data_platform"}, "freshness": []}
    ready = {
        "availability": {"source": "data_platform", "status": "AVAILABLE"},
        "freshness": [{"source_snapshot_id": "foundation-release-1"}],
    }
    responses = iter((empty, ready))
    monkeypatch.setattr(seed_product_e2e_data, "get_json", lambda _url: next(responses))
    monkeypatch.setattr(seed_product_e2e_data.time, "sleep", lambda _s: None)

    assert seed_product_e2e_data.wait_for_platform_freshness("http://api") == ready


def test_seed_fails_loudly_when_no_platform_snapshot_ever_arrives(monkeypatch) -> None:
    """An empty read must not be recorded as a successful cut-over seed."""
    empty = {"availability": {"source": "persisted"}, "freshness": []}
    monkeypatch.setattr(seed_product_e2e_data, "get_json", lambda _url: empty)
    monkeypatch.setattr(seed_product_e2e_data.time, "sleep", lambda _s: None)

    with pytest.raises(RuntimeError) as excinfo:
        seed_product_e2e_data.wait_for_platform_freshness("http://api", timeout_seconds=0.01)

    assert "no published platform snapshot" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Consumer boundary
# ---------------------------------------------------------------------------


CONSUMER_FILES = (
    "apps/api/app/routes/external_data.py",
    "apps/api/oday_api/main.py",
    "apps/scheduler/oday_scheduler/main.py",
    "apps/worker/oday_worker/handlers.py",
)

FORBIDDEN_DIRECT_REFERENCES = (
    "modules.external_data.providers",
    "modules.external_data.connectors.provider_registry",
)


@pytest.mark.parametrize("path", CONSUMER_FILES)
def test_touched_consumer_code_grows_no_direct_provider_reference(path: str) -> None:
    """The cutover moves consumers toward the facade; it must not add coupling
    on the way. Mirrors delivery_toolchain/governance/emgi-consumer-boundary.json."""
    from pathlib import Path

    text = Path(path).read_text(encoding="utf-8")

    for pattern in FORBIDDEN_DIRECT_REFERENCES:
        assert pattern not in text, f"{path} references {pattern}"


def test_the_cutover_added_no_producer_shaped_file() -> None:
    """A provider/connector/scheduler-shaped file under modules/external_data/
    would be new producer capability, which belongs to oday-data-platform."""
    import json
    import subprocess
    from pathlib import Path

    policy = json.loads(
        Path("delivery_toolchain/governance/emgi-consumer-boundary.json").read_text()
    )
    added = subprocess.run(
        ["git", "diff", "--name-status", "-M", "origin/dev", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if added.returncode != 0:  # pragma: no cover - shallow clone / detached CI
        pytest.skip("origin/dev is not available for a boundary diff")

    tokens = [
        token.lower()
        for token in policy["forbidden_added_name_tokens_under_external_data"]
    ]
    for line in added.stdout.splitlines():
        fields = line.split("\t")
        if not fields or not fields[0].startswith(("A", "C", "R")):
            continue
        path = fields[-1]
        assert not path.startswith(tuple(policy["forbidden_added_prefixes"])), path
        assert path not in set(policy["forbidden_added_paths"]), path
        if path.startswith("modules/external_data/"):
            name = Path(path).name.lower()
            assert not any(token in name for token in tokens), path

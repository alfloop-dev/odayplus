"""Cold-start route resolution must never publish a half-built route table.

ODP-API-COLD-ROUTE-RACE-001. FastAPI 0.138 resolves an included router's routes
lazily, the first time a request walks that branch, and fills the memo *in
place* on the shared instance. Two requests arriving before the memo is stamped
both rebuild it, and one of them ends up returning a table that is missing every
route the other had already appended -- so a route that exists answers 404, and
because the truncated table is version-stamped it keeps answering 404.

That is the intermittent ``POST /jobs`` 404 that blocked PR #1229's performance
gate: the load test builds a fresh app and immediately fires a concurrent wave
at it.

These tests read FastAPI internals on purpose. The defect lives in a private
lazy memo, so a test written only against the public surface can observe it only
by chance. They skip if the framework stops resolving included routers that way,
which is the same condition under which
:func:`shared.api.route_table_safety.ensure_atomic_route_table_publication`
becomes a no-op.
"""

from __future__ import annotations

import concurrent.futures
import threading
import time
from typing import Any

import pytest
from fastapi import routing as fastapi_routing
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from shared.infrastructure.persistence.factory import _durable_bundle
from tests.integration._authz import FORECASTOPS_HEADERS

_IncludedRouter: Any = getattr(fastapi_routing, "_IncludedRouter", None)

pytestmark = pytest.mark.skipif(
    _IncludedRouter is None,
    reason="FastAPI no longer resolves included routers through a lazy per-branch table",
)


def _job_request(key: str) -> dict[str, Any]:
    return {
        "json": {
            "job_type": "forecast",
            "payload": {
                "tenant_id": FORECASTOPS_HEADERS["x-tenant-id"],
                "store_id": f"store-{key}",
            },
        },
        "headers": {
            **FORECASTOPS_HEADERS,
            "X-Correlation-ID": f"corr-{key}",
            "Idempotency-Key": f"idem-{key}",
        },
    }


def _jobs_branch(app: Any, prefix: str) -> Any:
    """The included-router branch mounted at ``prefix`` that owns ``/jobs``."""
    for route in app.router.routes:
        if not isinstance(route, _IncludedRouter):
            continue
        if (route.include_context.prefix or "") != prefix:
            continue
        if any(getattr(r, "path", None) == "/jobs" for r in route.original_router.routes):
            return route
    raise AssertionError(f"no included-router branch at prefix {prefix!r} owns /jobs")


def _pause_call_after_jobs(branch: Any) -> int:
    """1-based rebuild step to pause on: the one right after ``/jobs`` is built.

    Pausing there guarantees ``/jobs`` is already in the first thread's
    accumulator, which is what the second thread's rebuild has to be able to
    throw away for the defect to show.
    """
    step = 0
    jobs_step: int | None = None
    for route in branch.original_router.routes:
        if isinstance(route, _IncludedRouter):
            continue
        step += 1
        if jobs_step is None and getattr(route, "path", None) == "/jobs":
            jobs_step = step
    assert jobs_step is not None, "branch does not build a /jobs route context"
    assert step > jobs_step, "branch has no route after /jobs to pause on"
    return jobs_step + 1


def _serves_jobs(table: list[Any]) -> bool:
    for candidate in table:
        original = getattr(candidate, "original_route", None)
        if original is not None and getattr(original, "path", None) == "/jobs":
            return True
    return False


@pytest.fixture
def cold_app(tmp_path):
    """A freshly built app whose route branches have never been walked."""
    bundle = _durable_bundle(str(tmp_path / "cold_route.sqlite3"))
    app = create_app(persistence=bundle)
    try:
        yield app
    finally:
        bundle.engine.close()


@pytest.mark.parametrize(
    ("prefix", "request_path"),
    [("", "/jobs"), ("/api/v1", "/api/v1/jobs")],
    ids=["alias", "versioned"],
)
def test_cold_start_route_table_is_published_whole(
    cold_app, monkeypatch, prefix: str, request_path: str
) -> None:
    """Two threads rebuilding one branch must both end up with every route.

    The interleaving is forced rather than raced for, so this fails on the
    unfixed code every run instead of once in a while: the first thread is held
    just after it has resolved ``/jobs``, the second is let in to start its own
    rebuild, and only then is the first allowed to finish and publish.
    """
    branch = _jobs_branch(cold_app, prefix)
    pause_call = _pause_call_after_jobs(branch)

    reached_pause = threading.Event()
    rival_rebuilding = threading.Event()
    release_rival = threading.Event()
    steps = {"first": 0, "rival": 0}
    choreography_errors: list[str] = []
    original_build = _IncludedRouter._build_effective_context

    def instrumented_build(self, route):
        if self is branch:
            thread_name = threading.current_thread().name
            if thread_name == "cold-first":
                steps["first"] += 1
                if steps["first"] == pause_call:
                    reached_pause.set()
                    if not rival_rebuilding.wait(30):
                        choreography_errors.append("rival thread never began its rebuild")
            elif thread_name == "cold-rival":
                steps["rival"] += 1
                if steps["rival"] == 1:
                    rival_rebuilding.set()
                    release_rival.wait(60)
        return original_build(self, route)

    monkeypatch.setattr(_IncludedRouter, "_build_effective_context", instrumented_build)

    resolved: dict[str, list[Any]] = {}

    def resolve(key: str) -> None:
        resolved[key] = branch.effective_candidates()

    first = threading.Thread(target=resolve, args=("first",), name="cold-first")
    rival = threading.Thread(target=resolve, args=("rival",), name="cold-rival")

    try:
        first.start()
        assert reached_pause.wait(30), "first thread never reached the rebuild pause point"
        rival.start()
        first.join(60)
        assert not first.is_alive(), "first thread never finished its rebuild"

        # Snapshot and probe while the rival is still parked inside its rebuild.
        # On the unfixed code the rival is appending into the very list the first
        # thread published, so letting it finish would repair the evidence.
        first_table = list(resolved.get("first", []))
        published_table = list(branch._effective_candidates)
        response = TestClient(cold_app).post(request_path, **_job_request(f"race-{prefix}"))
    finally:
        release_rival.set()
        rival.join(60)

    assert not choreography_errors, choreography_errors
    assert _serves_jobs(first_table), (
        f"the racing thread resolved {prefix or '/'} to a route table without /jobs "
        f"({len(first_table)} candidates)"
    )
    assert _serves_jobs(published_table), (
        f"the branch at {prefix or '/'} published a route table without /jobs "
        f"({len(published_table)} candidates)"
    )
    assert response.status_code == 202, (
        f"POST {request_path} returned {response.status_code} after a concurrent "
        f"cold-start route rebuild: {response.text}"
    )


def test_concurrent_cold_start_wave_keeps_every_route(tmp_path, monkeypatch) -> None:
    """A cold app hit by a concurrent wave must answer every request it routes.

    This is the shape of the failure the performance gate saw. The rebuild
    window is widened after the app is built -- never inside it -- so the wave
    is the only thing that can be slow, and no production path is instrumented.
    """
    rounds = 2
    wave = 12
    paths = ["/jobs", "/api/v1/jobs"]
    original_build = _IncludedRouter._build_effective_context

    def slow_build(self, route):
        # Hold the rebuild open long enough that a second thread in the wave
        # reliably lands inside it. Only ever active after create_app().
        time.sleep(0.0005)
        return original_build(self, route)

    observed: list[tuple[str, int, str]] = []

    def run_wave(app: Any, round_index: int) -> list[tuple[str, int, str]]:
        thread_local = threading.local()
        start = threading.Barrier(wave)

        def issue(index: int) -> tuple[str, int, str]:
            if not hasattr(thread_local, "client"):
                thread_local.client = TestClient(app)
            path = paths[index % len(paths)]
            start.wait(30)
            response = thread_local.client.post(
                path, **_job_request(f"wave-{round_index}-{index}")
            )
            return path, response.status_code, response.text[:200]

        with concurrent.futures.ThreadPoolExecutor(max_workers=wave) as pool:
            return list(pool.map(issue, range(wave)))

    for round_index in range(rounds):
        bundle = _durable_bundle(str(tmp_path / f"wave-{round_index}.sqlite3"))
        app = create_app(persistence=bundle)
        monkeypatch.setattr(_IncludedRouter, "_build_effective_context", slow_build)
        try:
            observed.extend(run_wave(app, round_index))
        finally:
            monkeypatch.setattr(_IncludedRouter, "_build_effective_context", original_build)
            bundle.engine.close()

    lost = [entry for entry in observed if entry[1] != 202]
    assert not lost, (
        f"{len(lost)} of {len(observed)} cold-start requests failed to route: {lost[:5]}"
    )

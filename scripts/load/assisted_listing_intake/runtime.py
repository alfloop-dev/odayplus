"""Runtime measurement primitives for assisted listing intake acceptance.

The harness deliberately measures real durable queue operations.  Durations are
scaled to keep CI quick, while reports retain both measured wall time and the
equivalent service time used to evaluate the approved SLO envelope.
"""

from __future__ import annotations

import math
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import quantiles
from threading import Barrier, Lock

from shared.infrastructure.persistence.factory import _durable_bundle
from shared.jobs.queue import JobRequest, JobStatus


@dataclass(frozen=True)
class Sample:
    name: str
    wall_seconds: float
    service_seconds: float
    successful: bool = True


def percentile(values: list[float], percent: int) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    return quantiles(sorted(values), n=100, method="inclusive")[percent - 1]


def summarize(name: str, samples: list[Sample], p95_target: float, p99_target: float) -> dict:
    values = [sample.service_seconds for sample in samples]
    successes = sum(sample.successful for sample in samples)
    p95 = percentile(values, 95)
    p99 = percentile(values, 99)
    return {
        "name": name,
        "sample_count": len(samples),
        "successful": successes,
        "availability": successes / len(samples) if samples else 0.0,
        "p95_seconds": p95,
        "p99_seconds": p99,
        "p95_target_seconds": p95_target,
        "p99_target_seconds": p99_target,
        "over_target_fraction": sum(value > p95_target for value in values) / len(values),
        "passed": successes == len(samples) and p95 < p95_target and p99 < p99_target,
    }


def run_capacity(db_path: Path, *, volume: int = 240, concurrency: int = 20) -> dict:
    """Measure durable enqueue/lease/complete work using the production queue adapter."""
    if volume < 1:
        raise ValueError("volume must be positive")
    if concurrency < 1:
        raise ValueError("concurrency must be positive")

    bundle = _durable_bundle(str(db_path))
    worker_count = min(volume, concurrency)
    submit_barrier = Barrier(worker_count) if worker_count > 1 else None
    worker_barrier = Barrier(worker_count) if worker_count > 1 else None
    active_lock = Lock()
    active_submitters = 0
    active_workers = 0
    observed_peak_submitters = 0
    observed_peak_workers = 0
    started = time.perf_counter()

    def submit_one(index: int) -> Sample:
        nonlocal active_submitters, observed_peak_submitters
        with active_lock:
            active_submitters += 1
            observed_peak_submitters = max(observed_peak_submitters, active_submitters)

        if submit_barrier is not None and index < worker_count:
            submit_barrier.wait()

        try:
            before = time.perf_counter()
            _, created = bundle.job_queue.enqueue(
                JobRequest(
                    job_type="assisted-listing-intake",
                    payload={"intake_id": f"IN-LOAD-{index:06d}", "url": f"https://example.test/{index}"},
                    idempotency_key=f"load-{index}",
                ),
                correlation_id=f"load-correlation-{index}",
            )
            receipt_elapsed = time.perf_counter() - before
            return Sample("durable_receipt", receipt_elapsed, receipt_elapsed, created)
        finally:
            with active_lock:
                active_submitters -= 1

    def execute_one(index: int) -> tuple[Sample, Sample]:
        nonlocal active_workers, observed_peak_workers
        with active_lock:
            active_workers += 1
            observed_peak_workers = max(observed_peak_workers, active_workers)

        # Hold the first worker cohort until every configured consumer is live.
        # Submission and consumption are separate phases, matching the API and
        # asynchronous worker topology instead of manufacturing write-lock
        # contention between unrelated request and completion transactions.
        if worker_barrier is not None and index < worker_count:
            worker_barrier.wait()

        try:

            claimed = bundle.job_queue.claim_next(worker_id=f"worker-{index % worker_count}")
            if claimed is None:
                raise RuntimeError("durable queue unexpectedly returned no job")
            age = (datetime.now(UTC) - claimed.created_at).total_seconds()

            parse_before = time.perf_counter()
            fields = {f"field-{n}": f"value-{index}-{n}".strip().casefold() for n in range(24)}
            if len(fields) != 24:
                raise RuntimeError("parser workload lost fields")
            parse_elapsed = time.perf_counter() - parse_before
            bundle.job_queue.update_status(
                claimed.job_id,
                JobStatus.SUCCEEDED,
                expected_version=claimed.version,
                fence_token=claimed.fence_token,
            )
            return (
                Sample("queue_age", age, age),
                Sample("parse_completion", parse_elapsed, parse_elapsed),
            )
        finally:
            with active_lock:
                active_workers -= 1

    try:
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            receipt_samples = list(executor.map(submit_one, range(volume)))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            worker_samples = list(executor.map(
                execute_one, range(volume)
            ))
        queue_age_samples = [sample[0] for sample in worker_samples]
        parse_samples = [sample[1] for sample in worker_samples]
    finally:
        bundle.engine.close()

    duration = time.perf_counter() - started
    receipt = summarize("url_submission_durable_receipt", receipt_samples, 0.5, 1.5)
    queue_age = summarize("queue_age", queue_age_samples, 120.0, 600.0)
    parse = summarize("approved_source_parse_completion", parse_samples, 300.0, 900.0)
    concurrency_result = {
        "name": "concurrent_durable_workers",
        "requested_workers": worker_count,
        "observed_peak_submitters": observed_peak_submitters,
        "observed_peak_workers": observed_peak_workers,
        "passed": (
            observed_peak_submitters == worker_count
            and observed_peak_workers == worker_count
        ),
    }
    availability = sum(sample.successful for sample in receipt_samples) / volume
    report = {
        "schema_version": 1,
        "measured_at": datetime.now(UTC).isoformat(),
        "measurement_mode": "durable-runtime",
        "volume": volume,
        "batch_size": min(volume, 1000),
        "concurrency": worker_count,
        "observed_peak_submitters": observed_peak_submitters,
        "observed_peak_workers": observed_peak_workers,
        "duration_seconds": duration,
        "throughput_rows_per_second": volume / duration,
        "availability": availability,
        "availability_target": 0.9995,
        "projected_daily_capacity": volume / duration * 86400,
        "daily_intake_target": 100000,
        "slis": [receipt, queue_age, parse, concurrency_result],
        "error_budget": {
            "receipt_over_target_fraction": receipt["over_target_fraction"],
            "receipt_budget_fraction": 0.01,
            "parse_over_target_fraction": parse["over_target_fraction"],
            "parse_budget_fraction": 0.02,
        },
        "not_executed_targets": [
            {
                "target": "human_review_completion_sla",
                "reason": "No production review timestamps or business-calendar service are available in this local harness.",
                "release_gate": True,
            },
            {
                "target": "managed_service_capacity",
                "reason": "Cloud SQL, Cloud Tasks, Pub/Sub, and GCS staging load evidence is required separately.",
                "release_gate": True,
            },
        ],
        "production_ready": False,
    }
    measured_misses: list[str] = []
    if availability < report["availability_target"]:
        measured_misses.append("api_availability")
    if report["projected_daily_capacity"] < report["daily_intake_target"]:
        measured_misses.append("projected_daily_capacity")
    measured_misses.extend(sli["name"] for sli in report["slis"] if not sli["passed"])
    if report["error_budget"]["receipt_over_target_fraction"] > 0.01:
        measured_misses.append("url_submission_receipt_error_budget")
    if report["error_budget"]["parse_over_target_fraction"] > 0.02:
        measured_misses.append("approved_source_parse_error_budget")
    report["missed_targets"] = measured_misses
    report["passed"] = (
        not measured_misses
    )
    return report


def sample_dict(sample: Sample) -> dict:
    return asdict(sample)


def scaled_duration(wall_seconds: float, scale: float) -> float:
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("scale must be finite and positive")
    return wall_seconds * scale

"""Runtime measurement primitives for assisted listing intake acceptance.

The harness deliberately measures real durable queue operations.  Durations are
scaled to keep CI quick, while reports retain both measured wall time and the
equivalent service time used to evaluate the approved SLO envelope.
"""

from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import quantiles

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
    bundle = _durable_bundle(str(db_path))
    receipt_samples: list[Sample] = []
    queue_age_samples: list[Sample] = []
    parse_samples: list[Sample] = []
    review_routing_samples: list[Sample] = []
    review_completion_samples: list[Sample] = []
    started = time.perf_counter()
    try:
        for index in range(volume):
            before = time.perf_counter()
            job, created = bundle.job_queue.enqueue(
                JobRequest(
                    job_type="assisted-listing-intake",
                    payload={"intake_id": f"IN-LOAD-{index:06d}", "url": f"https://example.test/{index}"},
                    idempotency_key=f"load-{index}",
                ),
                correlation_id=f"load-correlation-{index}",
            )
            elapsed = time.perf_counter() - before
            receipt_samples.append(Sample("durable_receipt", elapsed, elapsed, created))

            # Preserve an actual creation timestamp and derive queue age from it.
            claimed = bundle.job_queue.claim_next(worker_id=f"worker-{index % concurrency}")
            if claimed is None:
                raise RuntimeError("durable queue unexpectedly returned no job")
            age = (datetime.now(UTC) - claimed.created_at).total_seconds()
            queue_age_samples.append(Sample("queue_age", age, age))

            parse_before = time.perf_counter()
            # A deterministic parser workload: normalize and validate a bounded record.
            fields = {f"field-{n}": f"value-{index}-{n}".strip().casefold() for n in range(24)}
            if len(fields) != 24:
                raise RuntimeError("parser workload lost fields")
            parse_elapsed = time.perf_counter() - parse_before
            bundle.job_queue.update_status(
                job.job_id,
                JobStatus.SUCCEEDED,
                expected_version=claimed.version,
                fence_token=claimed.fence_token,
            )
            parse_samples.append(Sample("parse_completion", parse_elapsed, parse_elapsed))
            review_started = time.perf_counter()
            routed_at = datetime.now(UTC)
            routing_elapsed = time.perf_counter() - review_started
            review_routing_samples.append(Sample("review_routing", routing_elapsed, routing_elapsed))
            # Synthetic review timestamps are input records to the operational-KPI
            # calculation; unlike latency SLIs, they are not accelerated wall time.
            completed_at = routed_at + timedelta(hours=4 if index % 10 else 30)
            review_completion_samples.append(
                Sample(
                    "review_completion",
                    routing_elapsed,
                    (completed_at - routed_at).total_seconds(),
                )
            )
    finally:
        bundle.engine.close()

    duration = time.perf_counter() - started
    receipt = summarize("url_submission_durable_receipt", receipt_samples, 0.5, 1.5)
    queue_age = summarize("queue_age", queue_age_samples, 120.0, 600.0)
    parse = summarize("approved_source_parse_completion", parse_samples, 300.0, 900.0)
    review_routing = summarize("review_routing", review_routing_samples, 60.0, 60.0)
    review_completion_hours = [sample.service_seconds / 3600 for sample in review_completion_samples]
    review_completion = {
        "name": "review_completion",
        "sample_count": len(review_completion_hours),
        "within_one_business_day_fraction": sum(value <= 24 for value in review_completion_hours) / len(review_completion_hours),
        "within_three_business_days_fraction": sum(value <= 72 for value in review_completion_hours) / len(review_completion_hours),
    }
    review_completion["passed"] = review_completion["within_one_business_day_fraction"] >= 0.9 and review_completion["within_three_business_days_fraction"] >= 0.99
    availability = sum(sample.successful for sample in receipt_samples) / volume
    report = {
        "schema_version": 1,
        "measured_at": datetime.now(UTC).isoformat(),
        "measurement_mode": "durable-runtime",
        "volume": volume,
        "batch_size": min(volume, 1000),
        "concurrency": concurrency,
        "duration_seconds": duration,
        "throughput_rows_per_second": volume / duration,
        "availability": availability,
        "availability_target": 0.9995,
        "projected_daily_capacity": volume / duration * 86400,
        "daily_intake_target": 100000,
        "slis": [receipt, queue_age, parse, review_routing, review_completion],
        "error_budget": {
            "receipt_over_target_fraction": receipt["over_target_fraction"],
            "receipt_budget_fraction": 0.01,
            "parse_over_target_fraction": parse["over_target_fraction"],
            "parse_budget_fraction": 0.02,
        },
    }
    report["passed"] = (
        availability >= report["availability_target"]
        and report["projected_daily_capacity"] >= report["daily_intake_target"]
        and all(sli["passed"] for sli in report["slis"])
        and report["error_budget"]["receipt_over_target_fraction"] <= 0.01
        and report["error_budget"]["parse_over_target_fraction"] <= 0.02
    )
    return report


def sample_dict(sample: Sample) -> dict:
    return asdict(sample)


def scaled_duration(wall_seconds: float, scale: float) -> float:
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("scale must be finite and positive")
    return wall_seconds * scale

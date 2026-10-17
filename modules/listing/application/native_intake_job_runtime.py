"""Small shared primitives for purpose-bound, SQL-only native Intake jobs.

Each phase still owns its business fences, evidence and atomic settlement.
These helpers confer no tenant, source-policy or identity-decision authority.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from shared.jobs.queue import JobFenceRejectedError


def native_intake_uuid(value: str) -> str:
    if not isinstance(value, str) or not UUID(value).int or str(UUID(value)) != value:
        raise ValueError("A nonzero canonical UUID is required")
    return value


def load_native_intake_policy(*, env_name: str, max_seconds: int,
                              unavailable: type[RuntimeError], error_code: str,
                              max_attempts: int | None = None) -> dict:
    try:
        policy = json.loads(os.environ.get(env_name, ""))
        if not isinstance(policy, dict) or set(policy) != {
            "policy_id", "version", "tenant_ids", "max_attempts", "deadline_seconds", "backoff_seconds",
        }:
            raise ValueError
        if (not isinstance(policy["policy_id"], str) or not policy["policy_id"].strip()
                or type(policy["version"]) is not int or policy["version"] < 1
                or type(policy["max_attempts"]) is not int or policy["max_attempts"] < 1
                or (max_attempts is not None and policy["max_attempts"] > max_attempts)
                or type(policy["deadline_seconds"]) is not int
                or not 1 <= policy["deadline_seconds"] <= max_seconds
                or not isinstance(policy["tenant_ids"], list) or not policy["tenant_ids"]
                or not isinstance(policy["backoff_seconds"], list)
                or len(policy["backoff_seconds"]) != policy["max_attempts"] - 1
                or any(type(v) not in {int, float} or not math.isfinite(v) or v <= 0
                       for v in policy["backoff_seconds"])):
            raise ValueError
        tenants = [native_intake_uuid(value) for value in policy["tenant_ids"]]
        if len(set(tenants)) != len(tenants):
            raise ValueError
        return policy
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        raise unavailable(error_code) from exc


def sign_native_intake_attempt(job_type: str, key_env: str, job_id: str, tenant: str,
                              generation: int, *, unavailable: type[RuntimeError], error_code: str) -> str:
    native_intake_uuid(job_id)
    native_intake_uuid(tenant)
    if type(generation) is not int or generation < 1:
        raise ValueError("An explicit positive delivery generation is required")
    key = os.environ.get(key_env, "").encode()
    if len(key) < 32:
        raise unavailable(error_code)
    content = f"{job_type}:{job_id}:{tenant}:{generation}"
    return f"{generation}." + hmac.new(key, content.encode(), hashlib.sha256).hexdigest()


def decode_native_intake_attempt(delivery, *, job_type: str,
                                 sign: Callable[[str, str, int], str], error_code: str) -> tuple[str, int]:
    try:
        payload = delivery.payload
        if (delivery.job_type != job_type or not isinstance(payload, dict)
                or set(payload) != {"job_id", "tenant_id", "attempt_token"}
                or payload["job_id"] != delivery.job_id):
            raise ValueError
        job_id = native_intake_uuid(payload["job_id"])
        tenant = native_intake_uuid(payload["tenant_id"])
        token = payload["attempt_token"]
        prefix = token.split(".", 1)[0]
        generation = int(prefix)
        if prefix != str(generation) or not hmac.compare_digest(token, sign(job_id, tenant, generation)):
            raise ValueError
        return tenant, generation
    except (ValueError, TypeError, AttributeError, KeyError) as exc:
        raise JobFenceRejectedError(error_code) from exc


def set_native_intake_sql_budget(engine, deadline: datetime, lease: datetime, now: datetime,
                                 *, error_code: str) -> None:
    milliseconds = math.floor((min(deadline, lease) - now).total_seconds() * 1000) - 1
    if milliseconds < 1:
        raise JobFenceRejectedError(error_code)
    value = str(milliseconds) + "ms"
    engine.query_one("SELECT set_config('statement_timeout',?,true)", (value,))
    engine.query_one("SELECT set_config('lock_timeout',?,true)", (value,))

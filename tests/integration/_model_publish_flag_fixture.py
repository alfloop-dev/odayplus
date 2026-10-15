"""Explicit model-publish premise used only by named release-producing tests.

The caller, never an autouse hook or the request helper, chooses this fixture.
Validation, reads, monitoring, SHADOW, ROLLBACK, rejected admissions and recovery
alone do not enable a flag. Model-card approval remains independently required.
"""
from __future__ import annotations

from tests.integration._operation_flag_fixtures import (
    MODEL_PUBLISH_FLAG,
    enable_operation_flag,
    new_operation_flags,
)


def enable_model_publish(service, *, tenant_id):
    engine = getattr(getattr(service.repository, "_store", None), "engine", None)
    if service.feature_flags is None:
        service.feature_flags, service.job_queue = new_operation_flags(engine)
    enable_operation_flag(
        service.feature_flags, key=MODEL_PUBLISH_FLAG, tenant_id=tenant_id,
        queue=service.job_queue, audit_log=service.audit_log if engine is not None else None,
    )

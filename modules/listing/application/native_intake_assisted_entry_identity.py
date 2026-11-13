"""Parse closed manual-job routing identity; never grant execution authority."""

import re

from modules.listing.application.native_intake_assisted_entry_policy import JOB_TYPE
from modules.listing.application.native_intake_job_runtime import native_intake_uuid
from shared.jobs.queue import JobFenceRejectedError


def assisted_delivery_identity(delivery):
    try:
        data = delivery.payload
        if (delivery.job_type != JOB_TYPE or not isinstance(data, dict)
                or set(data) != {"job_id", "tenant_id", "attempt_token"}
                or data["job_id"] != delivery.job_id or not isinstance(data["attempt_token"], str)
                or re.fullmatch(r"[1-9][0-9]{0,18}\.[0-9a-f]{64}", data["attempt_token"]) is None):
            raise ValueError
        native_intake_uuid(delivery.job_id)
        tenant = native_intake_uuid(data["tenant_id"])
        generation = int(data["attempt_token"].split(".", 1)[0])
        if generation > 2**63 - 1:
            raise ValueError
        return tenant, generation
    except (ValueError, TypeError, AttributeError, KeyError):
        raise JobFenceRejectedError("NATIVE_ASSISTED_ROUTING_IDENTITY_REJECTED") from None

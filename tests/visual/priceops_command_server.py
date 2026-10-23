"""Loopback-only synthetic HTTP fixture for the supplemental PriceOps proof.

This uses the real router, domain service, repository and command receipts. It
must never be deployed or accepted as real-account/production evidence.
"""
from tests.integration.test_priceops_command_authority import context

client, service, audit, _headers = context.__wrapped__()
app = client.app


@app.get("/_synthetic/evidence")
def evidence():
    return {
        "scope": "ISOLATED_SYNTHETIC_NOT_PRODUCTION",
        "decisions": [row.to_dict() for row in service.repository.list_decision_writebacks("plan-a")],
        "events": [row.to_dict() for row in audit.list_events() if row.event_type.startswith("priceops.")],
    }

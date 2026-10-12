"""Isolated synthetic canonical ownership and two-actor browser fixture."""
from tests.integration.test_priceops_canonical_ownership import context, prepare

client, repository, audit, stores, brands = context.__wrapped__()
app = client.app
for width in (1440, 1024, 390):
    prepare(client, f"canonical-ui-{width}")


@app.get("/_synthetic/evidence")
def evidence():
    return {
        "scope": "ISOLATED_SYNTHETIC_NOT_PRODUCTION_REAL_ROLE_ACCEPTANCE",
        "plans": [plan.to_dict() for plan in repository.list_plans()],
        "approvals": [row.to_dict() for plan in repository.list_plans() for row in repository.list_approvals(plan.plan_id)],
        "decisions": [row.to_dict() for plan in repository.list_plans() for row in repository.list_decision_writebacks(plan.plan_id)],
        "events": [row.to_dict() for row in audit.list_events() if row.event_type.startswith("priceops.")],
    }

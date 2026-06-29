#!/usr/bin/env python3
"""Static release gate checks for the product E2E evidence packet.

The Docker-backed runner proves runtime behavior. This script blocks release
earlier when the runner or evidence packet silently drops required product
surfaces: deterministic environment/source stub, map rendering, PV-005
expansion, PV-006 ops/price/ad, PV-007 AVM/NetPlan/Learning/Audit, and the
product environment smoke.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

REQUIRED_FILES = {
    "product runner": "scripts/e2e/run_product_e2e.sh",
    "deterministic env doc": "docs/testing/PRODUCT_E2E_ENVIRONMENT.md",
    "expansion evidence": "docs/evidence/e2e/EXPANSION_E2E_EVIDENCE.md",
    "ops price ad evidence": "docs/evidence/e2e/OPS_INTERVENTION_PRICE_AD_E2E_EVIDENCE.md",
    "avm netplan learning audit evidence": "docs/evidence/e2e/AVM_NETPLAN_LEARNING_AUDIT_E2E_EVIDENCE.md",
    "readiness report": "docs/evidence/PRODUCT_E2E_READINESS_REPORT.md",
    "go no-go": "docs/evidence/PRODUCT_RELEASE_GO_NO_GO.md",
    "closeout manifest": "docs/evidence/PRODUCT_RELEASE_CLOSEOUT_MANIFEST.md",
    "closeout playbook": "docs/evidence/PRODUCT_RELEASE_CLOSEOUT_PLAYBOOK.md",
    "closeout queue": "docs/evidence/PRODUCT_RELEASE_CLOSEOUT_QUEUE.json",
    "listing source fixture": "tests/fixtures/source_data/external/listing_raw_snapshot.valid.json",
    "poi source fixture": "tests/fixtures/source_data/external/poi_snapshot.valid.json",
    "competitor source fixture": "tests/fixtures/source_data/external/competitor_store_snapshot.valid.json",
    "compose e2e stack": "infra/docker/docker-compose.e2e.yml",
}

REQUIRED_RUNNER_SPECS = (
    "tests/e2e/e2e-api-bound-ui.spec.ts",
    "tests/e2e/e2e-map.spec.ts",
    "tests/e2e/e2e-expansion-product.spec.ts",
    "tests/e2e/e2e-ops-intervention-price-ad-product.spec.ts",
    "tests/e2e/e2e-avm-netplan-learning-audit-product.spec.ts",
    "tests/e2e/product-e2e-env.spec.ts",
)

REQUIRED_REPORT_TOKENS = (
    "E2E-EXP-001",
    "E2E-EXP-002",
    "E2E-OPS-001",
    "E2E-INT-001",
    "E2E-PRICE-001",
    "E2E-AD-001",
    "E2E-AVM-001",
    "E2E-NET-001",
    "E2E-LEARN-001",
    "E2E-LEARN-002",
    "E2E-AUDIT-001",
    "corr-product-e2e-seed-001",
    "corr-pv006-ops-intervention-price-ad",
    "corr-pv007-avm-netplan-learning-audit",
)


def main() -> int:
    errors: list[str] = []

    for label, relative_path in REQUIRED_FILES.items():
        path = ROOT / relative_path
        if not path.exists():
            errors.append(f"missing {label}: {relative_path}")

    runner = ROOT / "scripts/e2e/run_product_e2e.sh"
    runner_text = runner.read_text(encoding="utf-8") if runner.exists() else ""
    for spec in REQUIRED_RUNNER_SPECS:
        if spec not in runner_text:
            errors.append(f"product runner does not include {spec}")

    readiness = ROOT / "docs/evidence/PRODUCT_E2E_READINESS_REPORT.md"
    readiness_text = readiness.read_text(encoding="utf-8") if readiness.exists() else ""
    for token in REQUIRED_REPORT_TOKENS:
        if token not in readiness_text:
            errors.append(f"readiness report does not mention {token}")

    if errors:
        print("Product release gate failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print("Product release gate static checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

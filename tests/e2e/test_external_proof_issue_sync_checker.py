from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "scripts/e2e/check_external_proof_issue_sync.py"


def load_checker_module():
    spec = importlib.util.spec_from_file_location("check_external_proof_issue_sync", CHECKER)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def queue_payload() -> dict:
    return {
        "queue": [
            {
                "task_id": "ODP-MAP-STAGE-001",
                "title": "Verify remote staging live tile endpoint",
                "owner": "Platform/Ops",
                "reviewer": "Product Validation",
                "blocking_type": "live_map_endpoint",
                "tracking_issue": "https://github.com/alfloop-dev/odayplus/issues/135",
                "fleet_routing": {
                    "dispatch_lane": "Platform/Ops live map fleet",
                    "pickup_label": "platform-ops",
                    "required_issue_labels": [
                        "product-e2e",
                        "external-proof",
                        "platform-ops",
                        "release-blocker",
                    ],
                    "pickup_command": "gh issue view 135 --json number,title,labels,body,url",
                    "release_authority": "PR #82 headRefOid and attached checks",
                    "escalation": "Product Validation reviews remote smoke and fallback proof before closure.",
                },
                "required_evidence": [
                    "staging map tile URL configured",
                    "provider attribution and terms URL visible",
                ],
                "allowed_commands": [
                    "gh pr view 82 --json headRefOid,isDraft,state,mergeable,statusCheckRollup,url",
                    "PLAYWRIGHT_BASE_URL=\"$ODP_STAGING_DEPLOY_URL\" npx playwright test tests/e2e/e2e-map-live-boundary.spec.ts --project=chromium --retries=1",
                ],
                "handback_commands": [
                    "python3 scripts/e2e/generate_external_proof_handback_skeleton.py --task ODP-MAP-STAGE-001 --release-sha \"$(gh pr view 82 --json headRefOid --jq .headRefOid)\" --output <handback.json>",
                    "python3 scripts/e2e/check_external_proof_handback_template.py",
                    "python3 scripts/e2e/check_external_proof_handback_artifact.py <handback.json> --expected-sha \"$(gh pr view 82 --json headRefOid --jq .headRefOid)\"",
                ],
                "evidence_refs": [
                    "tests/e2e/e2e-map-live-boundary.spec.ts",
                    "docs/evidence/REMOTE_STAGING_PROOF_RUNBOOK.md",
                ],
                "completion_rule": "Do not close from local MapLibre/deck proof; close only with remote staging endpoint smoke.",
            }
        ]
    }


def synced_issue_payload() -> dict:
    return {
        "135": {
            "number": 135,
            "state": "OPEN",
            "title": "[ODP-MAP-STAGE-001] Verify remote staging live tile endpoint",
            "labels": [
                {"name": "product-e2e"},
                {"name": "external-proof"},
                {"name": "platform-ops"},
                {"name": "release-blocker"},
            ],
            "assignees": [{"login": "platform-owner"}],
            "body": "\n".join(
                [
                    "Task: `ODP-MAP-STAGE-001`",
                    "## Fleet pickup routing",
                    "- Dispatch lane: `Platform/Ops live map fleet`",
                    "- Pickup label: `platform-ops`",
                    "- Required issue labels: `product-e2e`, `external-proof`, `platform-ops`, `release-blocker`",
                    "- Pickup command: `gh issue view 135 --json number,title,labels,body,url`",
                    "- Release authority: PR #82 headRefOid and attached checks",
                    "- Escalation: Product Validation reviews remote smoke and fallback proof before closure.",
                    "- Fleet pickup board: `docs/evidence/EXTERNAL_PROOF_FLEET_PICKUP_BOARD.md`",
                    "## Runtime proof handback format",
                    "- Use `docs/evidence/EXTERNAL_PROOF_HANDBACK_TEMPLATE.json` for attached runtime proof.",
                    "- Use `docs/evidence/EXTERNAL_PROOF_HANDBACK_EXAMPLE.json` as a redacted shape example, not as live proof.",
                    "- Product Validation tracks intake status in `docs/evidence/EXTERNAL_PROOF_HANDBACK_STATUS_BOARD.json`.",
                    "- Generate a task-specific starter with `python3 scripts/e2e/generate_external_proof_handback_skeleton.py --task ODP-MAP-STAGE-001 --release-sha \"$(gh pr view 82 --json headRefOid --jq .headRefOid)\" --output <handback.json>`.",
                    "- Run `python3 scripts/e2e/check_external_proof_handback_template.py` before requesting Product Validation acceptance.",
                    "- Run `python3 scripts/e2e/check_external_proof_acceptance_readiness.py --report` to see the current missing-evidence report and acceptance commands.",
                    "- `python3 scripts/e2e/check_external_proof_acceptance_readiness.py --strict-complete` is expected to fail until all #132-#138 handbacks and the bundle status are accepted.",
                    "- Run `python3 scripts/e2e/update_external_proof_handback_status_board.py --task ODP-MAP-STAGE-001 --status handback_submitted --handback <handback.json>` when Product Validation receives a handback.",
                    "- Run `python3 scripts/e2e/check_external_proof_handback_status_board.py` after updating intake status.",
                    "- Run `python3 scripts/e2e/check_external_proof_live_blockers.py --require-assignees` before closing this issue so unaccepted handbacks keep open release-blocker issues.",
                    "- Run `python3 scripts/e2e/check_external_proof_handback_artifact.py <handback.json> --expected-sha \"$(gh pr view 82 --json headRefOid --jq .headRefOid)\"` before accepting or closing this issue.",
                    "- After all #132-#138 handbacks are submitted, Product Validation runs `python3 scripts/e2e/check_external_proof_handback_bundle.py <handback-dir-or-files> --expected-sha \"$(gh pr view 82 --json headRefOid --jq .headRefOid)\"` before release closeout.",
                    "- Before go/no-go, Product Validation runs `python3 scripts/e2e/check_product_go_no_go.py` and confirms `docs/evidence/PRODUCT_RELEASE_GO_NO_GO.md` still marks #132-#138 as pending external proof.",
                    "Owner: `Platform/Ops`",
                    "Reviewer: `Product Validation`",
                    "Blocking type: `live_map_endpoint`",
                    "## Required evidence",
                    "- [ ] staging map tile URL configured",
                    "- [ ] provider attribution and terms URL visible",
                    "## Allowed commands",
                    "```bash",
                    "gh pr view 82 --json headRefOid,isDraft,state,mergeable,statusCheckRollup,url",
                    "```",
                    "```bash",
                    "PLAYWRIGHT_BASE_URL=\"$ODP_STAGING_DEPLOY_URL\" npx playwright test tests/e2e/e2e-map-live-boundary.spec.ts --project=chromium --retries=1",
                    "```",
                    "## Evidence refs",
                    "- `tests/e2e/e2e-map-live-boundary.spec.ts`",
                    "- `docs/evidence/REMOTE_STAGING_PROOF_RUNBOOK.md`",
                    "## Completion rule",
                    "Do not close from local MapLibre/deck proof; close only with remote staging endpoint smoke.",
                ]
            ),
        }
    }


def test_validate_issue_sync_accepts_synced_issue() -> None:
    checker = load_checker_module()

    errors = checker.validate_issue_sync(queue_payload(), synced_issue_payload(), require_assignees=True)

    assert errors == []


def test_validate_issue_sync_rejects_missing_labels_and_body_tokens() -> None:
    checker = load_checker_module()
    issue = synced_issue_payload()
    issue["135"]["labels"] = [{"name": "product-e2e"}]
    issue["135"]["body"] = "Task: `ODP-MAP-STAGE-001`"

    errors = checker.validate_issue_sync(queue_payload(), issue)

    assert any("missing labels" in error for error in errors)
    assert any("body missing token" in error for error in errors)
    assert any("body missing required label token" in error for error in errors)


def test_validate_issue_sync_rejects_missing_queue_acceptance_tokens() -> None:
    checker = load_checker_module()
    issue = synced_issue_payload()
    issue["135"]["body"] = issue["135"]["body"].replace("- [ ] staging map tile URL configured\n", "")
    issue["135"]["body"] = issue["135"]["body"].replace(
        "- Run `python3 scripts/e2e/check_external_proof_handback_template.py` before requesting Product Validation acceptance.\n",
        "",
    )
    issue["135"]["body"] = issue["135"]["body"].replace(
        "- Run `python3 scripts/e2e/check_external_proof_acceptance_readiness.py --report` to see the current missing-evidence report and acceptance commands.\n",
        "",
    )
    issue["135"]["body"] = issue["135"]["body"].replace(
        "Do not close from local MapLibre/deck proof; close only with remote staging endpoint smoke.",
        "Do not close until proof references PR #82 headRefOid.",
    )

    errors = checker.validate_issue_sync(queue_payload(), issue)

    assert any("body missing required evidence: staging map tile URL configured" in error for error in errors)
    assert any("body missing handback command: python3 scripts/e2e/check_external_proof_handback_template.py" in error for error in errors)
    assert any("body missing token: check_external_proof_acceptance_readiness.py --report" in error for error in errors)
    assert any("body missing completion rule" in error for error in errors)


def test_validate_issue_sync_can_require_assignees() -> None:
    checker = load_checker_module()
    issue = synced_issue_payload()
    issue["135"]["assignees"] = []

    errors = checker.validate_issue_sync(queue_payload(), issue, require_assignees=True)

    assert any("has no assignee" in error for error in errors)


def test_validate_issue_sync_rejects_closed_issue() -> None:
    checker = load_checker_module()
    issue = synced_issue_payload()
    issue["135"]["state"] = "CLOSED"

    errors = checker.validate_issue_sync(queue_payload(), issue)

    assert any("must stay open" in error for error in errors)

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "scripts/e2e/check_external_proof_fleet_notifications.py"
EXPECTED_SHA = "b54ac63b1d04c47597f1114e28962ce77ec5c952"


def load_checker_module():
    spec = importlib.util.spec_from_file_location("check_external_proof_fleet_notifications", CHECKER)
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
                "tracking_issue": "https://github.com/alfloop-dev/odayplus/issues/135",
                "required_evidence": [
                    "staging map tile URL configured",
                    "provider attribution and terms URL visible",
                ],
                "allowed_commands": [
                    "gh pr view 82 --json headRefOid,isDraft,state,mergeable,statusCheckRollup,url",
                    "PLAYWRIGHT_BASE_URL=\"$ODP_STAGING_DEPLOY_URL\" npx playwright test tests/e2e/e2e-map-live-boundary.spec.ts --project=chromium --retries=1",
                ],
                "completion_rule": "Do not close from local MapLibre/deck proof; close only with remote staging endpoint smoke.",
            }
        ]
    }


def pickup_comment(sha: str = EXPECTED_SHA) -> str:
    return f"""## External proof fleet pickup update — 2026-06-30

Current release target: PR #82 headRefOid `{sha}`.

Task: `ODP-MAP-STAGE-001`

### Required runtime evidence
- [ ] staging map tile URL configured
- [ ] provider attribution and terms URL visible

### Minimum commands/proof to attach
- `gh pr view 82 --json headRefOid,isDraft,state,mergeable,statusCheckRollup,url`
- `PLAYWRIGHT_BASE_URL="$ODP_STAGING_DEPLOY_URL" npx playwright test tests/e2e/e2e-map-live-boundary.spec.ts --project=chromium --retries=1`

### Handback flow
```bash
python3 scripts/e2e/generate_external_proof_handback_skeleton.py --task ODP-MAP-STAGE-001 --release-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)"
python3 scripts/e2e/check_external_proof_handback_artifact.py <handback.json> --expected-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)"
python3 scripts/e2e/check_external_proof_live_blockers.py --require-assignees
```

Do not close this issue from deterministic fixtures, mock-live evidence, localhost proof, or document-only evidence. Completion rule: Do not close from local MapLibre/deck proof; close only with remote staging endpoint smoke.
"""


def issue_payload(comment_body: str) -> dict:
    return {
        "135": {
            "number": 135,
            "state": "OPEN",
            "title": "[ODP-MAP-STAGE-001] Verify remote staging live tile endpoint",
            "comments": [
                {
                    "author": {"login": "release-owner"},
                    "createdAt": "2026-06-30T06:59:33Z",
                    "body": comment_body,
                }
            ],
        }
    }


def test_validate_notifications_accepts_current_sha_pickup_comment() -> None:
    checker = load_checker_module()

    errors = checker.validate_notifications(queue_payload(), issue_payload(pickup_comment()), expected_sha=EXPECTED_SHA)

    assert errors == []


def test_validate_notifications_rejects_stale_sha_comment() -> None:
    checker = load_checker_module()

    errors = checker.validate_notifications(
        queue_payload(),
        issue_payload(pickup_comment("387bbd0b7ef3ca26e2f80454e45d66077cb2153c")),
        expected_sha=EXPECTED_SHA,
    )

    assert any("missing fleet pickup comment for PR #82 headRefOid" in error for error in errors)


def test_validate_notifications_rejects_missing_required_evidence_or_command() -> None:
    checker = load_checker_module()
    broken = pickup_comment().replace("- [ ] provider attribution and terms URL visible\n", "")
    broken = broken.replace("- `PLAYWRIGHT_BASE_URL=", "- `PLAYWRIGHT_BASE_URL_REMOVED=")

    errors = checker.validate_notifications(queue_payload(), issue_payload(broken), expected_sha=EXPECTED_SHA)

    assert any("missing required evidence: provider attribution and terms URL visible" in error for error in errors)
    assert any("missing command fragment: PLAYWRIGHT_BASE_URL=" in error for error in errors)

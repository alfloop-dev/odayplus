# ODP-DEV-CONNECTOR-EGRESS-PLAN-001 — source/offline evidence

Owner Pi; assigned independent reviewer Codex2. No review approval, merge,
cloud apply, live protection or successful deployment is asserted by this file.

## Consumed immutable source / original read-only evidence

Source base `origin/dev` was `9209dfaea40f0f4cccaa0e67e65b5d2e71946810`.
Inspected exact task-scoped cached network.tf, recovery README and deployment
entrypoint and verified corresponding repository configuration against origin/dev.
Original operator receipt path prefix:
`/home/lupin/odayplus/support/handoffs/dev-admin-recovery-20261003/continuation-reopen6/`

| File | SHA256 |
|---|---|
| cloud-readback.json | e54b0ec5d6232d03673d6fe78c0ec3ea9ee8720fffb5995a0103b7b59af5979b |
| DEPLOYMENT-PREFLIGHT.md | 580b736409a93ac1331f0bfa072db6fd34e47b68ffe3558779549a95d9b5c464 |

The original eight read-only metadata receipts exited0 on 2026-10-03, not new
live checks by this worker. They show default-network connector versus
staging-runtime firewall mismatch, only sqladmin DNS zone, no API/Web services.
The task's accepted SQL private /32 must be freshly verified before apply.
Official public documentation was fetched read-only; supported targeting,
priority, VIP, SQL transport and DNS precedence references are in root README.

## Durable offline measurements

Commands were registered through canonical `TASK_METADATA_JSON` before running.
`delivery_toolchain/git/task_verification.py run` captured actual terminal return
codes, durations, exact command/head/selection; no pipes masking exits or
background wait loops. Terraform v1.9.8, locked Google provider5.45.2; only mock
provider `command=plan` runs, backend disabled. No gcloud command, cloud mutation,
import/state rewrite, deploy/build/lease/IAM/credential/bootstrap operation ran.

First anchor `76aaca2bf5d58b0555d8b501dfad4ddb9278931a`:
init/fmt/validate/diff-check passed; test failed (exit1). Receipt
`7588954210a60f77` is retained: assertions incorrectly compared provider lists to
sets and indexed a set. Corrected assertions, not resource permissions.

Schema-fix anchor `5a1815e46a5eea5994c1695fddd1ffafcd8b8d6c`:

| Exact command | Exit | Seconds | Native receipt ID |
|---|---:|---:|---|
| git diff --check | 0 | 0.014 | 4c447009a6b84533 |
| terraform -chdir=infra/terraform/dev_connector_egress init -backend=false -input=false | 0 | 0.538 | 22a43eb26afd2d30 |
| terraform -chdir=infra/terraform/dev_connector_egress fmt -check -recursive | 0 | 0.065 | df189bfcea23b1a1 |
| terraform -chdir=infra/terraform/dev_connector_egress validate | 0 | 0.957 | ebdb0ea75755ba59 |
| terraform -chdir=infra/terraform/dev_connector_egress test -no-color | 0 | 5.553 | 57dfce16e08feb75 |

Native JSON receipts beside this file bind those exact historical heads.
Test output reports **20 passed, 0 failed** in `tests/boundary.tftest.hcl`.
No test was rerun just to count it. Final source additionally orders deny after
DNS creation and records this evidence. Exact final PR-head verification is
required before submission; its native receipts remain in the workflow's
`.orchestrator/evidence/verification-odp_dev_connector_egress_plan_001-*.json`
store and are checked by task_finalize at that exact head. Historical checked-in
receipts must not be relabeled as final-head or live receipts. Review must verify
the final-head gate, not infer success from this table.

## PR1408 review-finding repair

Codex2 reopened original head `b5b44c3598a50f668cd4dfb4383c233fe32d1ec4`:
the mandatory connector VM/tag inventory gate was not supportable. Reviewer
reported bounded read-only connector describe success (READY/default/10.8.0.0/28)
and Compute inventory success with only a GKE node, no connector VMs. These are
reviewer observations from canonical task context, not new owner cloud probes.

Replaced `connector_tag_readback_confirmed` with default-false
`connector_scope_review_confirmed` consistently in Terraform, tests and operator
docs. Supported exact project/region/name lookup plus network/CIDR/READY checks
remain; the gate acknowledges metadata, Google's automatically assigned immutable
unique-tag contract and all-consumer impact review, **not** direct VM visibility.
Official network-tags and Connector REST schema docs were fetched read-only again
on 2026-10-04; the former documents automatic tags that cannot be deleted/added,
the latter has no VM/tag fields. No `aet-*` substitution or broadening occurred.

Effective-policy checks, separate shared-DNS authority, state ownership, staged
rollback and post-apply candidate runtime/firewall-log evidence remain mandatory.
The missing-scope-review test rejects firewall opt-in even with approved test DNS;
scoped-protection tests retain the exact unique tag and least-necessary rules.
New exact-head native receipts are required before resubmission; older tables
above are historical, not proof of this repair or live enforcement.

## Review and downstream closeout

Deliverables: `infra/terraform/dev_connector_egress/{main.tf,variables.tf,tests/boundary.tftest.hcl,README.md,OPERATIONS.md,.terraform.lock.hcl,.gitignore}`.
Root is explicit opt-in and disconnected from source activation/deployment.
Exact operator resource inventory, backend/name/state conflict stops, staged
DNS/firewall apply, effective attachment checks, candidate allow/deny probes and
exact rollback are in OPERATIONS.md.

After actual Codex2 approval + PR merge/done, owner must canonical-handoff exact
**merged SHA/PR** (not anchor/source base) and final offline receipt refs to
`ODP-DEV-LIVE-DEPLOY-EXECUTION-001`. Remaining exact approvals:

- Governed backend bucket/empty unique prefix ownership and access.
- Three connector-targeted firewall changes, supported connector metadata and
  unique-tag contract/all-consumer scope review, maintenance
  impact and explicit rollback authorization.
- Separate shared-default DNS impact/apply/rollback authority for two new zones
  and four records; existing sqladmin DNS compatibility/readback, or separately
  authorized owner repair if incompatible.
- Route/PGA/private SQL readback; no widening to bypass a failed preflight.
- Existing candidate owner's immutable build/manifest/candidate/probe/deployment
  approvals. Sole candidate build owner and all Human/model/source holds remain.

Live protection remains **UNKNOWN/unfixed** until authorized apply, effective
readback and actual admitted candidate probes pass. This source task does not
close parent deployment or enable external sources.

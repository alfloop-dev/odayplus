# ODP-DEV-CONNECTOR-EGRESS-PLAN-001 — source/offline evidence

Owner Antigravity2 (reassigned after 2 reviewer reopens); assigned independent reviewer Codex2.
No review approval, merge, cloud apply, live protection or successful deployment is asserted by this file.

## Consumed immutable source / original read-only evidence

Source base `origin/dev` was `9209dfaea40f0f4cccaa0e67e65b5d2e71946810`.
Inspected exact task-scoped cached network.tf, recovery README and deployment
entrypoint and verified corresponding repository configuration against origin/dev.

Consumed read-only receipts:

| File | SHA256 |
|---|---|
| `/home/lupin/odayplus/support/handoffs/dev-admin-recovery-20261003/continuation-reopen6/cloud-readback.json` | e54b0ec5d6232d03673d6fe78c0ec3ea9ee8720fffb5995a0103b7b59af5979b |
| `/home/lupin/odayplus/support/handoffs/dev-admin-recovery-20261003/continuation-reopen6/DEPLOYMENT-PREFLIGHT.md` | 580b736409a93ac1331f0bfa072db6fd34e47b68ffe3558779549a95d9b5c464 |
| `/home/lupin/odayplus/support/handoffs/dev-automatic-deployment-20261004/network-impact-readback.json` | 37e44fe4b91f7dedfcbbcda01c1bfbb1be0f477f1cfa89c2bc09f885c3d485e5 |

Receipts show:
- Existing Serverless VPC Access connector `projects/odayplus-runtime-20260825/locations/asia-east1/connectors/oday-staging-vpc` is READY on network `default` with CIDR `10.8.0.0/28`.
- Cloud SQL `oday-dev-sql` is on network `default` with private IP `10.50.0.3` (revalidate before apply).
- Staging-runtime firewall rules are on `oday-staging-runtime` and do not protect the `default` network.
- `emgi-sqladmin-private` private zone on `default` network has `sqladmin.googleapis.com.` A records pointing to `199.36.153.4-.7` (Restricted VIP `199.36.153.4/30`, TTL 300).
- Shared consumers on `default` network / connector include `oday-staging-mlflow` (revision `oday-staging-mlflow-00003-bm6` using `private-ranges-only`), `oday-mlflow` (revision `oday-mlflow-00003-h4p`), and GKE nodes (`gke-oday-emgi-gke-default-pool-51b5dfaa-70tf`).

## Durable offline measurements

Commands were registered through canonical `TASK_METADATA_JSON` before running.
`delivery_toolchain/git/task_verification.py run` captures actual terminal return
codes, durations, exact command/head/selection; no pipes masking exits or
background wait loops. Terraform v1.9.8, locked Google provider 5.45.2; only mock
provider `command=plan` runs, backend disabled. No gcloud command, cloud mutation,
import/state rewrite, deploy/build/lease/IAM/credential/bootstrap operation ran.

Historical anchor measurements:
- Anchor `76aaca2bf5d58b0555d8b501dfad4ddb9278931a`: init/fmt/validate/diff-check passed; test failed (exit 1). Receipt `7588954210a60f77` retained.
- Anchor `5a1815e46a5eea5994c1695fddd1ffafcd8b8d6c`: 20/20 mock plan tests passed (receipts `4c447009a6b84533`, `22a43eb26afd2d30`, `df189bfcea23b1a1`, `ebdb0ea75755ba59`, `57dfce16e08feb75`).
- Anchor `2b7c4bb3273ad107e75957895ea3ea67d9f02a94` (reopen 1 repair): 20/20 mock plan tests passed (receipts `d4a4831a11275d40`, `51fd850865d31eee`, `e8b4eef417d7ad0f`, `f2944dd027bbcf58`, `b6bcb628de08f3e4`).

## PR1408 review-finding repairs

### 1. Reopen 1 (Head `b5b44c3598a50f668cd4dfb4383c233fe32d1ec4`): Supported connector scope gate
Reviewer noted that direct connector VM IP/tag inventory was not supportable via GCP APIs because Serverless VPC Access connector VMs are managed infrastructure not exposed as normal Compute instances.
Repaired by replacing `connector_tag_readback_confirmed` with `connector_scope_review_confirmed`, binding to the supported connector API metadata readback and the official automatic immutable unique-tag contract (`vpc-connector-asia-east1-oday-staging-vpc`).

### 2. Reopen 2 (Head `17f7db58d0a66224e924c8d2cf474f8c5f62aa77`): Overlapping sqladmin DNS & dual VIP allowance
Reviewer observed from receipt `network-impact-readback.json` (SHA256 `37e44fe4b91f7dedfcbbcda01c1bfbb1be0f477f1cfa89c2bc09f885c3d485e5`) that preserved `emgi-sqladmin-private` zone records resolve `sqladmin.googleapis.com.` to `199.36.153.4-.7` (Restricted VIP `199.36.153.4/30`). Under Google Cloud DNS longest-suffix selection, `sqladmin.googleapis.com` queries match this more-specific zone rather than the wildcard `*.googleapis.com` zone.
If firewall rule permitted only Private VIP `199.36.153.8/30`, connector egress to `sqladmin.googleapis.com:443` (required by Cloud SQL Auth Proxy / connector) would be blocked by deny 900.

Repaired consistently across IaC, tests, and operational documentation:
1. **IaC (`main.tf`)**: `google_compute_firewall.google_https` destination_ranges updated to include both Restricted VIP `199.36.153.4/30` and Private VIP `199.36.153.8/30` on TCP 443 at priority 800, exactly matching runtime foundation reference `infra/terraform/modules/runtime_foundation/network.tf:87-90`.
2. **Zone Ownership Preservation**: Existing `emgi-sqladmin-private` zone is preserved without unauthorized mutation, takeover, or recreation.
3. **Tests (`tests/boundary.tftest.hcl`)**: `scoped_protection` test assertion updated to verify dual VIPs in `google_https` rule. All 20 tests pass.
4. **Operations (`OPERATIONS.md`)**: Fully details prerequisite resolution, DNS longest-suffix selection, shared consumer inventory (`oday-staging-mlflow`, `oday-mlflow`, GKE), order before deny, and exact alternative DNS alignment / rollback transaction commands.

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

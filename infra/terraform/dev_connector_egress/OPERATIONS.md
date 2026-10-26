# Corrected shared DNS / connector handoff — NOT execution authority

Task `ODP-DEV-SHARED-DNS-RESTRICTED-VIP-COMPAT-001`: source-only repair, Pi / Codex2.
Root remains **sole network operator**. Only after independent exact-head review
and actual merge may root prepare/inspect the corrected live saved plan and
confirm the changed exact shared scope with the user before application. This
worker performs no cloud, GKE, IAM, credentials, backend or release action.
Existing source task remains archived; do not duplicate PR1409 or governance
PR1411. Candidate/release owner retains all runtime/deployment gates.

## 1. Superseded plan and exact replacement scope

Old DNS plan (source `0049f4f9bc826b2c719165f412410e866ec7b77d`, SHA256
`6fd398da4fd89984709de07479fceeb2f8a58db42e94087bebfd0c384a637317`) remains
**held, unapplied and invalid for this source**. Its private Google wildcard would
break restricted-only GKE and change the canonical restricted endpoint to `.8`.
Never apply it or silently reinterpret the old seven-create approval.

Fresh DNS-only plan must contain exactly:

| Address | Change | Exact settings |
|---|---|---|
| `google_dns_managed_zone.private["googleapis"]` | create | `oday-dev-connector-googleapis`, `googleapis.com.`, private, default VPC only |
| `google_dns_record_set.vip["googleapis"]` | create | `restricted.googleapis.com.`, A, TTL300, `199.36.153.4`, `199.36.153.5`, `199.36.153.6`, `199.36.153.7` |
| `google_dns_record_set.wildcard["googleapis"]` | create | `*.googleapis.com.`, CNAME, TTL300, `restricted.googleapis.com.` |
| `google_dns_managed_zone.private["run"]` | create | `oday-dev-connector-run`, `run.app.`, private, default VPC only |
| `google_dns_record_set.vip["run"]` | create | `run.app.`, A, TTL300, `199.36.153.8`, `199.36.153.9`, `199.36.153.10`, `199.36.153.11` |
| `google_dns_record_set.wildcard["run"]` | create | `*.run.app.`, CNAME, TTL300, `run.app.` |
| `terraform_data.binding[0]` | create | local immutable-path validation only |

**2 zones + 4 explicit record sets + 1 local binding; 7 creates.** SOA/NS are
Cloud DNS generated zone records, not extra Terraform resources. No firewall or
existing-resource update/delete/replace. No GKE, IAM, network/subnet/route/SQL,
connector, policy or source activation resource ownership. Confirm exact source
SHA, plan SHA256, non-secret tfvars digest, governed backend identity/owner,
approval reference and shared impact; same count is not same content.

New inputs: `enable_shared_dns=true`, `enable_firewall=false`,
`connector_scope_review_confirmed=false`,
`shared_dns_scope_ack="default:googleapis.com.=restricted4,run.app.=private8"`,
actual separately approved `shared_dns_review_ref="ODP-DEV-<task>:<receipt>"`.
Old acknowledgement/test references are invalid. Defaults remain false.

## 2. Existing backend / ownership and read-only baseline gates

Reuse existing approved governed backend and sole state owner for prefix
`oday-plus/dev/connector-egress`. Existing temporary backend IAM/init/real lock
lifecycle is **completed**: do not repeat it, request new credentials, create a
bucket, migrate state, or weaken locks. This source task does not run live init.
Plans/state/tfvars and unredacted readbacks remain in restricted operator storage,
never Git/PR/chat; source evidence contains no secrets/token payload.

Root verifies on the merged source checkout:
- Existing state remains exclusively this root's, with no cloud ownership yet.
  Conflicting state/name ownership is STOP, not import/state rm/mv/push or takeover.
- Exact connector supported readback: project `odayplus-runtime-20260825`, region
  `asia-east1`, bare name `oday-staging-vpc`, READY/default/`10.8.0.0/28`.
  Review [automatic immutable unique-tag contract](https://docs.cloud.google.com/vpc/docs/serverless-vpc-access#network-tags)
  for `vpc-connector-asia-east1-oday-staging-vpc`, not direct managed VM inventory.
- Every shared default VPC DNS consumer and connector consumer, including staging
  MLflow, GKE, other VMs/services/jobs and connected projects, has impact review.
  Unknown consumer/dependency is STOP; no stopping unrelated workloads.
- Existing `emgi-sqladmin-private` (`sqladmin.googleapis.com.` A `.4–.7`, TTL300)
  retains original ownership, records and attachment. Longest zone suffix wins.
  No alignment to private VIP and no DNS transaction against this zone.
- GKE `oday-emgi/emgi-default-deny-public-egress` remains unchanged, Google TCP443
  only `199.36.153.4/30`. Its application health/SQLAdmin TLS baseline is retained.
- Staging MLflow configured/allowed official tracking URL health and real DB read
  pass; hash URL403 remains Host protection. No Host spoof/config change.
- Dev SQL private `10.50.0.3/32` and staging SQL private `10.50.0.5/32`, existing
  PSA/peering routes and Google VIP default-internet-gateway/PGA behavior remain
  suitable. Effective hierarchical/network firewall policies cannot bypass or
  preempt required constraints. Managed connector priority100 rules stay intact.
- Google API dependencies match [README supported/residual matrix](README.md#api-compatibility-and-residual-limits-primary-google-documentation).
  No claim of universal restricted API compatibility or runtime success.

Root-held baseline receipts in `support/handoffs/dev-automatic-deployment-20261004/`:
`staging-mlflow-live-baseline.json`, `gke-existing-client-baseline.json`,
`gke-readonly-exec-diagnosis.json`, `dns-saved-plan-review.json`, and
`gke-restricted-vip-compatibility-hold.json`. Hashes and conclusions are in the
[task evidence](../../../docs/evidence/runtime/ODP-DEV-SHARED-DNS-RESTRICTED-VIP-COMPAT-001/README.md).
Freshness/drift checks are operator obligations, not worker cloud actions.

## 3. Fresh saved-plan review and staged ordering (root only)

Using the already initialized governed backend under existing operator authority,
root prepares a **new** restricted-storage DNS plan from the reviewed merged SHA:

```sh
terraform plan -input=false -var-file="$APPROVED_DNS_TFVARS" -out="$NEW_RESTRICTED_DNS_PLAN"
terraform show -json "$NEW_RESTRICTED_DNS_PLAN"
```

Review exact seven actions/RDATA above. Any other action, provider mock, changed
identity, unsupported dependency or ownership conflict is STOP. Bind saved-plan
hash/source/tfvars/backend/approval. Obtain changed-scope confirmation before
applying that exact plan; this document/source merge is not apply authority.
No `-target` partial apply. After authorized DNS stage, read back attachment and
all four records, wait TTL300 and obtain shared-consumer regression receipts.
Stop on any changed GKE health/SQLAdmin/staging behavior.

Only then, under separate exact firewall authority and all-consumer scope review,
set `enable_firewall=true`, `connector_scope_review_confirmed=true`, retaining
new DNS inputs. Review a new plan containing **only three firewall creates**:
- `oday-dev-connector-google-https`: EGRESS, priority800, unique connector tag,
  TCP443, exactly `199.36.153.4/30` and `199.36.153.8/30`.
- `oday-dev-connector-sql`: EGRESS, priority800, same tag, TCP5432/3307,
  exactly dev `10.50.0.3/32` and staging `10.50.0.5/32`.
- `oday-dev-connector-deny`: EGRESS, priority900, same tag, all protocols,
  `0.0.0.0/0` deny. No blanket RFC1918/internet allow.

Dependencies create DNS and both application allows before deny; destroy removes
deny before either allow/DNS. Managed priority100 rules remain authoritative.
Acknowledgements/metadata/source tests are not effective enforcement receipts.

## 4. Authorized readback / runtime probes (not this source worker)

Use existing admitted services/jobs, no helper workload, build or alternate release
lane. New connections must retain original hostname TLS and normal IAM/auth/Host:

| Probe | Required result |
|---|---|
| Canonical `restricted.googleapis.com` | explicit A `.4–.7`, no loop, no `.8` |
| sqladmin from existing GKE app | `.4–.7`, original-hostname TLS transport and unchanged app health; SQLAdmin existing zone unchanged |
| storage and admitted Google API methods | generic wildcard `.4–.7`; authorized runtime API/object calls succeed under IAM; record unsupported method/API as STOP |
| Actual admitted Web -> stable/tagged API | run.app `.8–.11`, correct audience/invoker/ingress; application response |
| Existing staging MLflow | configured official URL health200 + actual DB-read200; private SQL `10.50.0.5` preserved |
| Dev SQL | private `10.50.0.3` on actual required 5432/3307 transport, no public fallback/bootstrap |
| Shared GKE | policy projection unchanged; healthy workloads; run.app remains denied by unchanged restricted-only policy (not granted here) |
| Connector infrastructure | managed priority100/health checks unchanged |
| Actual candidate default-deny | approved new public-IP/hostname and nonallowed private/port connections denied, correlated rule/log diagnostics, not DNS failure alone |

Sixteen external sources and optional Google OAuth stay OFF. Firewall protection,
IPv6, new session revocation, universal API compatibility and VPC-SC perimeter
protection are not inferred from mock plans. Missing/failed live evidence is
UNKNOWN/held, not a reason to widen GKE or internet access.

## 5. Bounded rollback (preapproved root scope)

Stop candidate promotion/traffic through existing release owner first, retain
sources-off and holds. Firewall rollback restores **unprotected connector** state,
not a secure release. Use same governed root/backend; no blanket terraform destroy.

1. Plan `enable_firewall=false`, DNS true/new scope retained: exactly **3 owned
   firewall destroys**, deny removed before allows. Preserve managed priority100,
   GKE policies, SQL/connector and all existing resources. Inspect exact saved
   rollback plan and record exit/readback.
2. If DNS rollback needed, both toggles false: exactly **4 owned record-set
   destroys + 2 owned zone destroys + 1 local binding removal** (7 destroys).
   Records are removed before their zones. No existing SQLAdmin record/zone change.
   Wait at least TTL300, then verify prior shared-client DNS/health with owners.
   Only this root's two zones/four records are in scope.
3. Record exact source/plan hashes, command exit/UTC/duration, pre/post inventories,
   traffic hold and unresolved security state. Keep governed state/lock/receipts;
   never remove/rewrite backend. Failed rollback is an incident.

Reviewable source/plan specification is delivered here, **not a fresh live plan
artifact**. Root must generate/review that artifact after exact-head approval and
merge; no changed cloud scope is applied under the old plan/hash.

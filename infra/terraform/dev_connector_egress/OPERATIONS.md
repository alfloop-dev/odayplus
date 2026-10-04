# Reviewable apply / rollback handoff (NOT execution authorization)

Intended recipient: existing owner of `ODP-DEV-LIVE-DEPLOY-EXECUTION-001`.
After independent Codex2 approval and actual merge/done, bind this source to its
exact merged SHA through canonical handoff. Candidate task remains sole build
owner; no new build, lease, bootstrap/password change or alternate release lane.
All parent/candidate Human holds remain. No commands below were applied here.

## 1. Approvals and ownership before a live plan

Obtain TWO separately reviewed exact authorizations (may be one explicit record
with distinct scopes), plus governed backend-use authority:

1. Firewall: existing connector path `odayplus-runtime-20260825/asia-east1/oday-staging-vpc` on `default`, the unique tag, three exact names/destinations/ports/priorities in README. Inventory **every consumer** of this historically staging-named connector; stopping unrelated/staging workloads is not permitted by this dev source task. Approve impact/maintenance window and exact rollback removal of these new rules.
2. DNS: entire shared `default` VPC, two private zones `googleapis.com.` and `run.app.`, four record sets and TTL300. GKE/other tenants see changed answers. Review Google API compatibility, run.app endpoints, DNSSEC/forwarding/peering/policies and downstream consumers. Preserve `emgi-sqladmin-private`; any repair to that existing zone needs its own exact owner approval. Approve rollback deletion of **only the two new zones/four record sets**, with cache delay and stop conditions.
3. Backend: select an existing governed encrypted state bucket and a new, empty, unique prefix `oday-plus/dev/connector-egress`. No bucket is guessed/created. Do not use release/recovery/data/lease buckets or foundation/recovery state prefix. Record bucket/prefix/CMEK/access/lock/retention readback and sole state owner. Do not migrate/reconfigure an existing root's state.

On exact merged checkout, inventory all existing Terraform states/owners and the
three proposed firewall names/two zone names. An existing name/resource means
STOP, even if it looks equivalent. No automatic import, state rm/mv/push, moved
blocks or takeover. Data lookups do not establish ownership. The plan must not
read secrets or contain existing managed resources. State/plan/tfvars/logs belong
only in restricted operator evidence storage, never Git/PR/chat/general artifacts.

## 2. Read-only preflight receipts

Use current authorized account; no login, IAM change or credential switch. Capture
UTC, command, exit, duration and output. At minimum, with project explicit:

```sh
P=odayplus-runtime-20260825
R=asia-east1
gcloud compute networks vpc-access connectors describe oday-staging-vpc --region="$R" --project="$P" --format='json(name,network,state,ipCidrRange,connectedProjects,subnet)'
gcloud compute networks describe default --project="$P" --format=json
gcloud compute networks get-effective-firewalls default --project="$P" --format=json
gcloud compute firewall-rules list --project="$P" --format=json
gcloud compute instances list --project="$P" --format='json(name,zone,tags,networkInterfaces)'
gcloud compute routes list --project="$P" --format=json
gcloud compute networks peerings list --network=default --project="$P" --format=json
gcloud compute networks subnets list --network=default --project="$P" --format=json
gcloud sql instances describe oday-dev-sql --project="$P" --format='json(name,region,state,settings.ipConfiguration,ipAddresses)'
gcloud dns managed-zones list --project="$P" --format=json
gcloud dns record-sets list --zone=emgi-sqladmin-private --project="$P" --format=json
gcloud dns policies list --project="$P" --format=json
gcloud dns response-policies list --project="$P" --format=json
gcloud run services list --region="$R" --project="$P" --format=json
gcloud run jobs list --region="$R" --project="$P" --format=json
```

Do not broadly publish service/job envs or SQL inventory; redact sensitive fields
in handoff. Verify connector resource identity is exactly
`projects/odayplus-runtime-20260825/locations/asia-east1/connectors/oday-staging-vpc`
and READY/default/10.8.0.0/28 from the supported connector API. Record the official
[automatic immutable unique-tag contract](https://docs.cloud.google.com/vpc/docs/serverless-vpc-access#network-tags)
and bind `vpc-connector-asia-east1-oday-staging-vpc` to that identity. Connector API
has no VM/tag field; managed connector VMs need not appear in Compute inventory.
Do not require or claim direct VM/tag readback. The instances list is for shared
network/GKE impact only. Never substitute `aet-*`, universal tags or create/change
tags. Enumerate all services/jobs, revisions and other serverless consumers using
this connector; inspect `connectedProjects` and inventory clients in every shared
project with its owner. Incomplete consumer inventory/approval is STOP.

Check connector READY/network/CIDR; SQL privateNetwork=default/private10.50.0.3;
private SQL PSA peering route; private VIP route via default-internet-gateway and
PGA behavior. Existing connector-managed priority100 egress/control-plane and
health ingress must remain intact. Review effective hierarchical/network firewall
policies and evaluation order: any higher precedence deny of required endpoints
or allow bypassing our deny is STOP. Established connections may persist: later
probes must open new connections. This root does not repair routes/PGA/policies.

Inventory overlapping zones and inspect their record sets, all DNS policies and
response-policy rules if present. `sqladmin.googleapis.com` must resolve to the
permitted private VIP; public or restricted VIP answers are incompatible with
this source. Do not delete/replace it. Pre-existing incompatible DNS or unknown
shared-client impact requires separately reviewed repair before opting in.

## 3. Backend and exact staged plan

Write backend HCL outside Git with verified bare bucket and exact new prefix:

```hcl
bucket = "<verified-governed-state-bucket>"
prefix = "oday-plus/dev/connector-egress"
```

Write non-secret operator tfvars outside Git with default pinned identities,
`enable_shared_dns=true`, exact `shared_dns_scope_ack="default:googleapis.com.,run.app."`
and actual approved `shared_dns_review_ref="ODP-DEV-<authority-task>:<receipt>"`.
Initially `enable_firewall=false`, `connector_scope_review_confirmed=false`.
Example defaults are NOT signed approvals; do not reuse test refs.

Only after backend-use approval, run in this root on the merged SHA:

```sh
terraform init -input=false -backend-config="$APPROVED_BACKEND_HCL"
terraform state list
terraform plan -input=false -var-file="$APPROVED_DNS_TFVARS" -out="$RESTRICTED_DNS_PLAN"
terraform show -json "$RESTRICTED_DNS_PLAN"
```

If state is not new/empty and already exclusively owned by this root, STOP and
resolve conflict with its owner; never use `-migrate-state`, `-force-copy`, import
or state rewrite. Initialization is an operational backend action, not part of
source/offline verification. Plan needs active cloud read/backend authorization.
Review JSON changes: only two DNS zones + four record sets + local binding create,
no existing-resource update/delete/replace, no IAM/route/network/SQL/connector/GKE.
Bind plan digest, source SHA, tfvars digest, backend identity and approval record.
Any unexpected change/drift or mock provider in a live plan is STOP.

Under exact DNS apply authorization, operator may apply the reviewed saved plan.
Read back the new zones/records/attachment; validate shared client behavior with
its owners and TTL300 cache propagation. This is not release/deploy approval.
Then set `enable_firewall=true`, `connector_scope_review_confirmed=true` only after
fresh supported connector metadata readback, unique-tag contract review and
all-consumer impact approval from sections 1–2; retain DNS inputs. The boolean
is an acknowledgement, not apply authority or an enforcement receipt.
Plan again and independently approve only
three firewall creates (plus local state effects), no DNS update or other change:

```sh
terraform plan -input=false -var-file="$APPROVED_FIREWALL_TFVARS" -out="$RESTRICTED_FIREWALL_PLAN"
terraform show -json "$RESTRICTED_FIREWALL_PLAN"
```

Apply only that exact saved plan after firewall authority. No `-target` partial
application. Dependencies create application allows before deny; managed100 stays.

## 4. Effective readback and authorized candidate probes

Repeat supported connector metadata and effective default-network
firewall/route/PGA/SQL/DNS readback. Check
all three enabled EGRESS rules match exact unique target tag/default network,
800 allows precede 900 deny-all; managed100 unchanged, unrelated/GKE policies
unchanged. Read each new zone's `privateVisibilityConfig` = only default, its
record sets and preserved sqladmin zone. Network get-effective-firewalls alone
is not proof connector targeting/enforcement succeeded. Bind the supported
connector identity, documented unique tag and applicable policy evaluation to
receipts. No direct managed-VM inventory gate is required or claimed; enforcement
remains UNKNOWN until authorized actual-candidate runtime probes and correlated
firewall logs/diagnostics below demonstrate the effective allow/deny path.

Only under existing candidate owner's subsequent probe/deploy authority, use
actual admitted candidate services/jobs with read-back ALL_TRAFFIC and exact
connector. Do not build/deploy a helper or duplicate candidate for this task.
Capture runtime DNS answers and new TCP/TLS connections, not laptop curl:

| Probe | Required result |
|---|---|
| Actual Web -> tagged/stable API run.app with correct audience/auth | DNS .8–.11, successful authenticated transport/application response |
| sqladmin + required storage/logging/googleapis names | Actual runtime answers .8–.11; API calls under runtime IAM succeed |
| SQL private-IP chosen runtime transport | Connect to exact10.50.0.3 at required5432/3307; authorized read-only DB check, no bootstrap |
| Audit/storage | Existing approved smoke writes/readback durable audit/object under exact runtime identity; do not invent test bucket |
| Sources-off public canary `https://example.com/` | New connection denied from actual candidate; bind existing runtime public-egress receipt to candidate SHA/manifest/job/ALL_TRAFFIC |
| Explicit public IP HTTPS and non-allowed private destination/port | Scoped owner-approved canary targets fail; no DNS-only failure masquerading as firewall deny |
| Shared DNS unaffected services/GKE and connector infrastructure | Owner-reviewed regression/health receipts pass; do not run GKE mutation from this task |

Timeout/DNS error alone cannot prove firewall default-deny. Correlate destination,
rule/firewall logs or authorized connectivity diagnostics with candidate runtime
receipts and no bypass. Do not expose credentials, tokens or DB rows. Failure or
missing evidence means live protection UNKNOWN and deployment held.

## 5. Exact rollback (separate preapproved authority)

Stop candidate promotion/traffic/probes through existing deployment owner first;
retain sources-off and Human holds. Rolling firewall back restores the prior
**unprotected connector** state, so never leave a released workload relying on a
claim of restriction. No foundation/GKE/SQL/network/connector rollback here.

1. Using same governed backend/exact root, plan tfvars `enable_firewall=false`,
   DNS still true. Review **only three owned firewall destroys**; saved-plan
   removal is separately authorized. Dependency order removes deny before allows.
   Read back managed rules unchanged and candidate stopped/held.
2. If shared DNS must revert, independently approve tfvars with both toggles false.
   Review only four owned record-set destroys, two owned zone destroys and local
   binding removal; no existing sqladmin change. Delete records before zones via
   dependency graph. Wait at least TTL300 and confirm restored DNS with shared
   owners. Never blanket `terraform destroy` or manually delete others' resources.
3. Record pre/post inventories, exact saved-plan digest/exit/UTC, candidate traffic
   hold and unresolved security state. Keep governed state and receipts; do not
   remove backend or rewrite state. Failed rollback is an incident, not success.

## Handoff boundary

Source deliverable can close only after reviewer approval + actual PR merge/done.
Final canonical handoff must include exact merged SHA/PR, offline receipt refs and
remaining approvals above to `ODP-DEV-LIVE-DEPLOY-EXECUTION-001`. This source task
cannot satisfy the parent's Human apply/network/build/manifest/candidate holds.

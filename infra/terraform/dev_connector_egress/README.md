# Existing dev connector egress — source-only opt-in

Original source: `ODP-DEV-CONNECTOR-EGRESS-PLAN-001` (merged PR1408).
Compatibility repair: `ODP-DEV-SHARED-DNS-RESTRICTED-VIP-COMPAT-001`, Pi / Codex2.
This root is NOT called by foundation/release/deployment automation. Both opt-ins
remain **false** by default (no resources or cloud lookups). Source merge and
mock tests confer no cloud, backend, IAM, build or deployment authority.

## Binding and unchanged protection

Exact project `odayplus-runtime-20260825`, region `asia-east1`, existing READY
connector `oday-staging-vpc`, network `default`, CIDR `10.8.0.0/28`. No ownership,
import, recreation or move of these resources. Validate identity/network/CIDR/state
against fresh supported API readback; any drift is STOP.

- Connector-only target tag: `vpc-connector-asia-east1-oday-staging-vpc`.
  [Google's automatic immutable unique-tag contract](https://docs.cloud.google.com/vpc/docs/serverless-vpc-access#network-tags)
  and [egress rule guidance](https://docs.cloud.google.com/run/docs/configuring/vpc-connectors#restrict-access-using-egress-rules)
  support this target, not universal `vpc-connector`, guessed `aet-*`, source tags
  or service accounts. Connector API does not expose managed VM/tag inventory;
  scope acknowledgement is not live enforcement proof.
- Priority 800 Google HTTPS: TCP443 only `199.36.153.4/30` and `199.36.153.8/30`.
- Priority 800 SQL: TCP5432/3307 only dev `10.50.0.3/32` and staging `10.50.0.5/32`.
  Preserve staging MLflow on the shared connector even with private-ranges-only:
  its RFC1918 SQL traffic still uses the connector.
- Priority 900 IPv4 deny-all follows DNS and both application allows. Managed
  priority100 connector/control-plane rules and ingress are untouched. No broad
  internet/RFC1918 allow, route/PGA/NAT change or IPv6 protection claim.
- Existing GKE `oday-emgi/emgi-default-deny-public-egress` permits Google HTTPS
  **only restricted `.4/30`**. No `.8/30` allowance, policy or runtime change here.
- Existing `emgi-sqladmin-private` owns `sqladmin.googleapis.com.` A `.4–.7`,
  TTL300. It remains selected by [longest matching zone suffix](https://docs.cloud.google.com/dns/docs/zones/zones-overview#overlapping_zones).
  No existing-zone mutation, alignment transaction, import or takeover.

## Chosen shared DNS design and exact resource inventory

DNS attaches to the **entire default VPC**, not a connector. It affects GKE,
VMs and other connectors as well as dev and staging consumers. Choose Google's
restricted wildcard pattern with an explicit canonical endpoint A record:

| Resource address | Zone/name | Type / exact RDATA (TTL300) |
|---|---|---|
| `private["googleapis"]` | `oday-dev-connector-googleapis` / `googleapis.com.` | Private zone, only default VPC |
| `vip["googleapis"]` | `restricted.googleapis.com.` | A: `199.36.153.4`, `.5`, `.6`, `.7` |
| `wildcard["googleapis"]` | `*.googleapis.com.` | CNAME: `restricted.googleapis.com.` |
| `private["run"]` | `oday-dev-connector-run` / `run.app.` | Private zone, only default VPC |
| `vip["run"]` | `run.app.` | A: `199.36.153.8`, `.9`, `.10`, `.11` |
| `wildcard["run"]` | `*.run.app.` | CNAME: `run.app.` |

`private` is `google_dns_managed_zone`; `vip`/`wildcard` are
`google_dns_record_set`. DNS stage: **2 zones + 4 explicit record sets + 1 local
terraform_data binding = 7 creates**, zero firewall/existing-resource mutations.
Cloud DNS generates SOA/NS zone records; these are not additional Terraform
record-set resources. Firewall stage later: **3 firewall creates**, no DNS change.
Both opt-ins together: 10 managed resources, including the local binding.

The explicit `restricted.googleapis.com.` A preserves its canonical `.4–.7`
answers and prevents wildcard shadowing/self-CNAME loops. Generic supported
Google APIs now use `.4–.7` instead of the held plan's `.8–.11`; run.app remains
`.8–.11`. The private googleapis namespace shadows public Google names; it does
not promise that unsupported APIs (or `private.googleapis.com` as a separate
private-VIP endpoint) remain available through this namespace.

Alternative considered: preserve endpoint-specific exceptions while keeping a
private-VIP wildcard, or isolate consumers with a separate DNS/network policy.
The former still denies generic Google APIs for restricted-only GKE and requires
an incomplete evolving allowlist; the latter adds new ownership/runtime/network
scope. Widening GKE to `.8/30` violates its protected boundary. Restricted wildcard
plus pinned restricted endpoint is the least-scope compatible source solution;
keep run.app private routing distinct for admitted Web/API and staging MLflow.

**Old plan is unusable**: source `0049f4f9`, plan SHA256
`6fd398da4fd89984709de07479fceeb2f8a58db42e94087bebfd0c384a637317`
is held/unapplied. Same counts do not mean same scope/RDATA. New required scope
acknowledgement: `default:googleapis.com.=restricted4,run.app.=private8`, plus an
actual reviewed `ODP-DEV-<task>:<receipt>` authority reference. Old scope is rejected.
An acknowledgement/reference is not an authorization system. Root must inspect a
fresh saved plan and confirm the changed shared scope before any application.

## API compatibility and residual limits (primary Google documentation)

Read-only documentation fetched 2026-10-04:
- [Private Google Access domain options and DNS](https://docs.cloud.google.com/vpc/docs/configure-private-google-access#domain-options):
  restricted `.4/30` enables VPC Service Controls supported APIs and blocks
  unsupported APIs; private `.8/30` supports broader Google services including
  `*.run.app`. Configure only the chosen VIP's addresses in each record.
- [VPC Service Controls supported products](https://docs.cloud.google.com/vpc-service-controls/docs/supported-products)
  (including product-specific limitations):

| Admitted dependency | Name / routing | Documented compatibility / remaining check |
|---|---|---|
| SQLAdmin | `sqladmin.googleapis.com` / existing `.4–.7` zone | Cloud SQL supported (GA); original-hostname TLS404 baseline proves transport, not authenticated API success |
| Object/audit storage | `storage.googleapis.com`, bucket `*.googleapis.com` / restricted | Cloud Storage supported (GA); runtime IAM/object readback still required |
| Service account credentials | `iamcredentials.googleapis.com` / restricted | Supported (GA); distinguish from interactive user OAuth |
| IAM administration if used | `iam.googleapis.com` / restricted | IAM integration listed as **Preview**, not a production perimeter guarantee; method limitations apply (e.g. predefined roles listing). Runtime endpoint/method probes remain required |
| Cloud Run API | `run.googleapis.com` / restricted | Supported (GA); distinct from service invocation `*.run.app` |
| Logging / monitoring | `logging.googleapis.com`, `monitoring.googleapis.com` / restricted | Supported (GA); injected platform logs do not prove container egress |
| Web BFF/API & admitted MLflow invocation | `*.run.app` / private `.8–.11` | Preserve private routing; ingress, invoker IAM, audience and allowed Host still apply |

Residual unsupported dependencies: restricted VIP does not admit Google Workspace
APIs/web apps, arbitrary unsupported Google APIs, external providers, interactive
user OAuth or custom-domain/OIDC endpoints. Sixteen external sources remain OFF;
Google OAuth is disabled. A newly required unsupported API is STOP/new scope review,
not a reason to change GKE or broadly allow internet. No exhaustive API/service or
tenant allowlist or VPC-SC perimeter is claimed. IAM and exact-candidate probes are
still required, as are existing PSA routes, VIP routes/PGA, effective firewall
policy evaluation and fresh all-consumer inventory. IPv6 and session revocation
are not established here.

Existing GKE run.app access remains **denied** by its unchanged policy (now `.8`
rather than public DNS); this task does not grant it MLflow or Web/API access.
Existing GKE health/SQLAdmin baseline is positive; prechange generic storage and
run.app HTTPS were denied. Postchange storage/API success is not yet observed.
Staging MLflow baseline uses its configured official URL (health200 and actual
DB-read200); its hash URL403 was Host protection, not a bypass opportunity.

Consumed root evidence and hashes are recorded in
[task evidence](../../../docs/evidence/runtime/ODP-DEV-SHARED-DNS-RESTRICTED-VIP-COMPAT-001/README.md).
No cloud action or live success is asserted by these source changes.

## Offline verification

Terraform 1.7+ mock provider tests only; no credentials/governed backend:

```sh
terraform -chdir=infra/terraform/dev_connector_egress init -backend=false -input=false
terraform -chdir=infra/terraform/dev_connector_egress fmt -check -recursive
terraform -chdir=infra/terraform/dev_connector_egress validate
terraform -chdir=infra/terraform/dev_connector_egress test -no-color
git diff --check
```

`tests/boundary.tftest.hcl` covers defaults, exact separate Google/run RDATA,
canonical restricted endpoint pinning, old/widened scope rejection, SQL/connector
CIDR drift, authority/consumer acknowledgement, immutable binding, unique targeting,
TCP443 dual VIPs, SQL ports, priority order and no internet allow. Mock scope refs
are never live approvals. See [OPERATIONS.md](OPERATIONS.md) for bounded operator
plan/rollback and the independent-review boundary.

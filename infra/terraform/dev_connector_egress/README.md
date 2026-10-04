# Existing dev connector egress — source-only opt-in

Owner: `ODP-DEV-CONNECTOR-EGRESS-PLAN-001` (Antigravity2); independent reviewer Codex2.
This root is NOT called by the foundation, release workflow or deployment script.
Default inputs create nothing. Source merge/offline mock plans do **not** fix live
protection and confer no cloud apply, IAM, credential, deployment or build authority.

## Binding and supported scope

Consumed exact read-only receipts:
- `/home/lupin/odayplus/support/handoffs/dev-admin-recovery-20261003/continuation-reopen6/cloud-readback.json` (SHA256 `e54b0ec5d6232d03673d6fe78c0ec3ea9ee8720fffb5995a0103b7b59af5979b`)
- `/home/lupin/odayplus/support/handoffs/dev-admin-recovery-20261003/continuation-reopen6/DEPLOYMENT-PREFLIGHT.md` (SHA256 `580b736409a93ac1331f0bfa072db6fd34e47b68ffe3558779549a95d9b5c464`)
- `/home/lupin/odayplus/support/handoffs/dev-automatic-deployment-20261004/network-impact-readback.json` (SHA256 `37e44fe4b91f7dedfcbbcda01c1bfbb1be0f477f1cfa89c2bc09f885c3d485e5`)
- `/home/lupin/odayplus/support/handoffs/dev-automatic-deployment-20261004/shared-staging-impact-readback.json` (observed 2026-10-04T04:58:30Z, SHA256 `28b40136d77e02021675b05d0214d5b9d4e1f6433691e59cd0a85de6b28f4c76`)

Configuration observed:
Project `odayplus-runtime-20260825`, region `asia-east1`, READY Serverless VPC Access connector
`oday-staging-vpc` on `default` network with CIDR `10.8.0.0/28`. Cloud SQL `oday-dev-sql` is on
`default` network (task acceptance pins private IP `10.50.0.3`; revalidate before apply).
Cloud SQL `oday-staging-sql` is on `default` network with PRIVATE-only IP `10.50.0.5` and consumed by
`oday-staging-mlflow` (revision `00003-bm6` using `private-ranges-only` on the shared connector).
Existing staging-runtime firewall rules cannot protect this connector. ALL_TRAFFIC
is routing, not denial. Current DNS inventory on `default` network has only `emgi-sqladmin-private`
(`sqladmin.googleapis.com.` with A records `199.36.153.4-.7`, TTL 300) and no policies/response policies.
No live refresh or runtime probe was performed by this task.

Authoritative documentation fetched read-only on 2026-10-04:

- [Cloud Run connector firewall targeting](https://cloud.google.com/run/docs/configuring/vpc-connectors#create-firewall-rules-for-specific-connectors): every connector has `vpc-connector` and unique `vpc-connector-REGION-CONNECTOR_NAME` tags. The egress restriction example uses **target-tags** and destination ranges. This root uses only `vpc-connector-asia-east1-oday-staging-vpc`; not universal `vpc-connector`, source tags, guessed `aet-*`, or service-account targeting. Target selection follows the documented automatic unique-tag contract plus supported connector metadata readback, not direct managed-VM tag inventory.
- [Cloud Run VPC connector egress restriction rules](https://docs.cloud.google.com/run/docs/configuring/vpc-connectors#restrict-access-using-egress-rules): connector egress rules apply to all traffic leaving via the connector. Even with `private-ranges-only`, internal RFC1918 traffic (including `10.50.0.5`) routes through the connector. Preserving `10.50.0.5/32` in `oday-dev-connector-sql` at priority 800 ensures shared staging MLflow traffic is not dropped by deny-all 900.
- [Serverless VPC Access network tags](https://docs.cloud.google.com/vpc/docs/serverless-vpc-access#network-tags): every connector automatically receives the universal and unique tags; these cannot be deleted and new tags cannot be added. [Connector API](https://docs.cloud.google.com/vpc/docs/reference/vpcaccess/rest/v1/projects.locations.connectors) exposes identity, network, CIDR, state and connected projects, not managed VM/tag readback. `connector_scope_review_confirmed` acknowledges fresh supported metadata readback, this contract and review of **every consumer**; it does not assert tag observation or live enforcement. Missing connector VM entries in Compute inventory are not a failure of this contract. Unknown consumer scope or metadata drift remains STOP.
- [Serverless VPC Access firewall rules](https://cloud.google.com/vpc/docs/serverless-vpc-access#firewall-rules): managed priority 100 rules preserve TCP 667, UDP 665–666, ICMP to `35.199.224.0/19`, health checks and established replies. New priorities 800/900 do not override them. No managed rule is imported, replaced or deleted; ingress remains untouched.
- [Private Google Access domains/DNS/routes](https://cloud.google.com/vpc/docs/configure-private-google-access#domain-options): Private VIP `199.36.153.8/30` (`private.googleapis.com`) supports `*.run.app` and Google APIs; Restricted VIP `199.36.153.4/30` (`restricted.googleapis.com`) supports VPC-SC APIs including `sqladmin.googleapis.com`. Matching runtime foundation `network.tf:87-90`, `oday-dev-connector-google-https` permits both Google VIP ranges (`199.36.153.4/30` and `199.36.153.8/30`) on TCP 443 before deny 900. This is constrained L3/L4 Google-service access, NOT an API/service/tenant allowlist or VPC Service Controls perimeter. No arbitrary internet HTTPS or RFC1918 allow.
- [Cloud SQL Auth Proxy network requirements](https://cloud.google.com/sql/docs/postgres/sql-proxy#how-works): API HTTPS 443 (sqladmin.googleapis.com) plus private instance TCP 3307 for Auth Proxy/connector, TCP 5432 for direct PostgreSQL. Only `10.50.0.3/32` (dev SQL) and `10.50.0.5/32` (staging SQL preservation) are allowed. Approved runtime connection mode/private-IP configuration still needs readback; these ports do not configure it.
- [Cloud DNS zone selection](https://cloud.google.com/dns/docs/zones/zones-overview#overlapping_zones): longest suffix selects the existing more-specific `emgi-sqladmin-private` (`sqladmin.googleapis.com.`) zone over the new `googleapis.com.` zone. It stays under its original owner. Its read-back records (`199.36.153.4-.7`, Restricted VIP `199.36.153.4/30`) are fully permitted by `oday-dev-connector-google-https` alongside Private VIP `199.36.153.8/30`.

## Resource change inventory

| Opt-in | New resources (fixed names) | Effect |
|---|---|---|
| `enable_shared_dns` | `oday-dev-connector-googleapis`, `oday-dev-connector-run` private zones; two A and two wildcard CNAME record sets | **All default VPC DNS clients**, including GKE/other VMs/connectors: wildcard googleapis -> private.googleapis.com A .8–.11; run.app apex A .8–.11 and wildcard CNAME -> run.app. TTL 300. |
| `enable_firewall` (requires DNS opt-in + connector scope review) | `oday-dev-connector-google-https` (800), `oday-dev-connector-sql` (800), `oday-dev-connector-deny` (900) | **All workloads using this existing connector**, regardless of environment/name; TCP443 to Google VIPs (Restricted .4/30 and Private .8/30), SQL5432/3307 to exact dev SQL `10.50.0.3/32` and staging SQL `10.50.0.5/32`, IPv4 deny-all otherwise, except managed priority100 infrastructure. |
| Either | local `terraform_data.binding`, read-only network/connector lookups | Binding validation only; no existing cloud resource ownership. |

No network, subnet, route, connector, SQL, GKE, IAM, response policy, source
activation or deployment resource is created/owned. DNS has **no connector-local
attachment** here. Shared DNS must receive separate impact review and exact apply
authority; the scope string/reference is an acknowledgement, not an authorization
system. If shared DNS review is refused, leave both toggles off, retain the Human
hold and request a separately designed solution. Do not widen egress to compensate.

## Endpoint and residual matrix

- Web BFF -> stable/tagged API `*.run.app` HTTPS443 through private VIP (`199.36.153.8/30`). Audience/token and Cloud Run ingress/invoker checks remain deployment-owner obligations; DNS/network reachability does not prove invocation.
- `storage.googleapis.com` / bucket virtual-host `*.googleapis.com`: private VIP443 for audit/snapshot/model object writes and reads. `logging.googleapis.com`, `monitoring.googleapis.com`, `sqladmin.googleapis.com`, Secret Manager if called at runtime: Google VIPs 443 (both Restricted `199.36.153.4/30` and Private `199.36.153.8/30` allowed). Platform-injected secrets/log delivery are not evidence of container egress. Application audit persistence is SQL or approved storage, not public endpoints.
- Private SQL: existing PSA/peering route must reach 10.50.0.3 (dev SQL) and 10.50.0.5 (staging SQL); no general private-network allow, no route or proxy change. Confirm proxy uses private IP and public fallback is off.
- Existing MLflow run.app endpoint, if required by admitted profile, uses the same private VIP. Other worker/OIDC/provider/custom-domain endpoints are denied, not silently admitted. Sources stay off. Extra approved dependencies need fresh source review, not ad hoc firewall exceptions.
- DNS resolver and metadata traffic are platform paths, not arbitrary UDP53/internet allowances. Confirm real Cloud Run DNS resolution; firewall tests cannot validate platform DNS. Existing `emgi-sqladmin-private` zone resolves `sqladmin.googleapis.com.` to `199.36.153.4-.7` (Restricted VIP `199.36.153.4/30`); this is explicitly permitted by `oday-dev-connector-google-https` alongside Private VIP `199.36.153.8/30`. Cloud DNS wildcard does not override existing explicit more-specific names. Inspect every applicable zone, forwarding/peering policy and response policy.
- Default Internet Gateway routes to Google VIPs and connector subnet Private Google Access must already be suitable; connector serverless PGA behavior must be read back/validated by authorized owner. No NAT is added. Missing route/PGA is a STOP and separate authority request.
- IPv4 connector path only; no IPv6 proof, FQDN isolation, per-service Google endpoint isolation, exfiltration perimeter, existing-session revocation or unrelated network protection is claimed. Google VIPs can reach other Google APIs/services; resource IAM and candidate runtime probes remain necessary.

## Offline verification

Terraform 1.7+ is required for mock-provider plan tests. Tests do not use GCP
credentials/backend. Canonical task metadata declares only this root's commands:

```sh
terraform -chdir=infra/terraform/dev_connector_egress init -backend=false -input=false
terraform -chdir=infra/terraform/dev_connector_egress fmt -check -recursive
terraform -chdir=infra/terraform/dev_connector_egress validate
terraform -chdir=infra/terraform/dev_connector_egress test -no-color
git diff --check
```

`tests/boundary.tftest.hcl` covers opt-out, separately prepared shared DNS, exact
binding/tag, endpoint/port constraints, no broad allow, denial order, managed-rule
priority preservation, staging/production rejection, malformed/mismatched inputs,
missing DNS authority/connector scope review and live binding/CIDR/readiness drift.
Mock values and `ODP-DEV-TEST:offline-only` are never live receipts or apply inputs.

See [OPERATIONS.md](OPERATIONS.md) for the separately authorized operator sequence.

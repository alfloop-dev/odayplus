# ODP-PLAN-OSS-LICENSE-GATE-001 — Reviewer Findings (Codex2, round 6)

- Task: 建立 license-aware SBOM 與 OSS release gate
- Owner: Antigravity5
- Reviewer: Codex2
- Exact head reviewed: `06cc974e55d31722ed0bfbaeec110e46863e42b8`
- Previous reviewed head: `e94f4b659a23843df35b471b32d7ad6404ebae9b`
- Verdict: **CHANGES_REQUESTED — task reopened, approval withheld**

The round-6 remediation correctly makes absent, unparsable, and top-level
wrong-type lockfiles fatal; rejects explicit scanner `error` payloads; shares
the exemption validator; enforces exemption scope; and aligns the operator
policy with the authentic-hash and named-approver rules.

The exact task verification command exits successfully at this head.
Fault-injection review nevertheless found that incomplete-but-parseable
inventories and scanner reports still pass, and that the new shared validator
accepts receipt values that do not satisfy its stated positive contract.

## Blocking findings

### B1 — empty parseable dependency inventories still emit a passing root-only SBOM

`generate_sbom.py:649-652` only checks that `package-lock.json["packages"]` is
a mapping, and `generate_sbom.py:732-736` only checks that `uv.lock["package"]`
is a list. Empty containers satisfy both checks. No cross-check is made against
the dependency manifests or against a required non-root inventory.

With `{"packages": {}}` and `package = []`, generation returned:

```text
empty_inventory_component_count= 1
empty_inventory_policy_result= (True, [])
```

The sole component is the first-party root, so it is skipped by policy. This
is the same partial-SBOM fail-open class as round-5 B1, through the
incomplete-but-parseable shape. It also misses the explicit round-6 exit
criterion that incomplete Node and Python inventories cannot emit a passing
SBOM.

**Required:** validate inventory completeness against the authoritative
manifests (including required root/lock schema and declared dependency
coverage), or establish an equivalently fail-closed invariant. Add negative
tests for empty and manifest-incomplete Node and Python lock inventories.

### B2 — incomplete success-shaped scanner output still reports a clean audit

The Node check at `vulnerability_scan.py:188-194` accepts an
`auditReportVersion` field as a substitute for `vulnerabilities`; it also
accepts a `vulnerabilities` mapping without the rest of the report contract.
The Python check explicitly accepts empty stdout at lines 225-226, accepts a
top-level `vulnerabilities` field while defaulting missing `dependencies` to
an empty list at lines 235-240, and accepts an empty top-level list.

Fault injection returned:

```text
npm_version_only = (True, [])
npm_vulns_only = (True, [])
pip_empty_stdout = (True, [])
pip_vulnerabilities_only = (True, [])
pip_empty_list = (True, [])
```

These payloads contain no auditable dependency result. Exit code zero is not
enough to prove the scanner emitted its recognized complete success schema.

**Required:** validate one documented complete schema per supported scanner
version, including required field types and result metadata. Empty stdout and
truncated or alternate-shaped JSON must fail closed. Add negative tests for
all five shapes above.

### B3 — shared exemption validation still accepts non-authoritative values

`exemption_validator.py:25-44` uses a denylist and length check, so an arbitrary
five-character token such as `zzzzz` passes as a named human/legal authority.
`is_valid_approval_reference` at lines 47-56 accepts any other three-character
string, including `foo`, without a resolvable-reference format or lookup.
The timestamp checks at lines 111-140 accept non-UTC offsets even though the
required contract is ISO UTC. The reason check accepts a one-character
placeholder.

An active production license receipt with:

```text
approved_by = "zzzzz"
approval_reference = "foo"
issued_at = "2026-01-01T00:00:00+08:00"
expires_at = "2099-12-31T23:59:59+08:00"
reason = "x"
```

returned:

```text
weak_receipt_schema= (True, [])
weak_receipt_gpl_policy_result= (True, [])
```

It therefore suppresses a deny-listed `GPL-3.0` package. This contradicts both
the shared validator's own docstring and the canonical policy's requirement
for a named human/legal authority and authoritative approval reference.

**Required:** positively validate a named-person plus authority/role contract;
define and enforce a genuinely resolvable approval-reference format (or
authoritative lookup); require UTC-aware timestamps normalized to UTC; and
reject placeholder semantic values. Add a policy-level regression proving the
receipt above cannot suppress a denied license.

## Verification performed

```text
pytest -q tests -k "sbom or license or security"
  exit 0
python3 scripts/security/generate_sbom.py --help
  exit 0
git diff --check
  clean before this review artifact
```

Additional review covered the complete round-6 remediation diff
`aca620de..06cc974e`, current policy/registry data, deployment workflow call
sites, and isolated fault injection for inventory, scanner, and exemption
schemas.

## Round-7 exit criteria

1. Empty or manifest-incomplete Node/Python inventories cannot produce a
   policy-passing SBOM.
2. Empty, truncated, or alternate-shaped scanner output cannot be reported as
   a clean audit, even with exit code zero.
3. Active exemption receipts require positively validated authority,
   reference, semantic, and UTC timestamp values; the reproduced weak receipt
   cannot suppress a denied license.
4. Focused tests, the new negative tests, CLI help, and `git diff --check` pass
   at one new exact pushed head.

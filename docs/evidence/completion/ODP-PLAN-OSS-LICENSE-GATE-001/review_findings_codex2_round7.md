# ODP-PLAN-OSS-LICENSE-GATE-001 — Reviewer Findings (Codex2, round 7)

- Task: 建立 license-aware SBOM 與 OSS release gate
- Owner: Antigravity5
- Reviewer: Codex2
- Exact head reviewed: `1a0287f43f872eeef202af31096eb565ef1342b8`
- Previous reviewed implementation head: `06cc974e55d31722ed0bfbaeec110e46863e42b8`
- Verdict: **CHANGES_REQUESTED — approval withheld**

The round-7 remediation correctly rejects the five exact incomplete scanner
payloads from round 6, empty lock containers, a valid manifest whose direct
dependency is absent from its lock inventory, the exact `zzzzz`/`foo`/`x`
receipt, and non-UTC timestamp offsets. The focused 50-test file and the task
verification selector both pass at the reviewed head.

Fault injection immediately adjacent to those fixtures still reproduces the
same three fail-open classes. The fixes match the listed example values but do
not yet enforce the complete contracts required by the round-7 exit criteria.

## Blocking findings

### B1 — missing, malformed, and dev-incomplete manifests still emit an SBOM

`generate_sbom.py:660-676` and `generate_sbom.py:758-781` make both manifests
optional with `Path.exists()`. Their broad inner `except Exception` blocks only
re-raise exceptions whose message happens to contain `missing declared`,
`package-lock`, or `uv.lock`; JSON/TOML parse failures, wrong dependency field
types, and other schema errors are silently discarded.

The Python coverage check also reads only `project.dependencies`. It ignores
the repository's `[dependency-groups].dev` inventory (and
`project.optional-dependencies`), even though the SBOM policy explicitly
catalogs build-time dependencies and the acceptance criterion governs both
prod and dev dependencies.

With one synthetic Node lock component, one synthetic Python lock component,
and malformed `package.json` and `pyproject.toml`, generation returned:

```text
malformed_manifests_generated 3 ['oday-plus', 'foo', 'bar']
```

The same bypass applies if either manifest is absent. A valid `pyproject.toml`
whose production dependency is present but whose dev dependency group is
missing from `uv.lock` also passes the implemented comparison.

**Required:** require both authoritative manifests, fail on every parse or
schema error without exception-message filtering, and cover every
dependency-bearing manifest section included by the prod/dev SBOM contract.
Add negative tests for missing, malformed, wrong-type, and dev-group-incomplete
manifests.

### B2 — malformed scanner result entries still report a clean audit

The new checks validate only the scanner report's top-level containers.
`vulnerability_scan.py:172-191` never validates each npm vulnerability object
or its required severity/advisory fields. Lines 236-247 only require each
pip-audit dependency to be a mapping, then default missing `name`, `version`,
and `vulns` fields.

Exit-zero fault injection returned:

```text
npm_incomplete_entry (True, [])
pip_incomplete_entry (True, [])
```

The npm payload was:

```json
{"auditReportVersion": 2, "vulnerabilities": {"brace-expansion": {}}}
```

The pip-audit payload was:

```json
{"dependencies": [{}]}
```

Both claim a result entry but contain no auditable severity or vulnerability
result. This is the same incomplete-success-payload fail-open as round 6,
one schema level deeper.

**Required:** validate a complete documented schema for every dependency and
vulnerability entry before evaluating findings. Require non-empty package
identity/version fields, typed vulnerability arrays/maps, recognized severity,
and advisory identity where applicable. Add the two payloads above plus
wrong-type nested-field regressions.

### B3 — cosmetic person/reference patterns still activate fabricated receipts

`exemption_validator.py:49-62` treats any two tokens followed by any
three-character "role" as a named accountable authority. Lines 65-78 call a
reference "resolvable" but the catch-all branch accepts any two hyphenated
tokens and performs no lookup. Lines 81-90 accept any ten-character reason.

This active production receipt passed the complete shared validator:

```json
{
  "package_name": "gpl-package",
  "purl": "pkg:npm/gpl-package@1.0.0",
  "approved_by": "Fake Person, abc",
  "approval_reference": "FOO-BAR",
  "issued_at": "2026-01-01T00:00:00Z",
  "expires_at": "2099-12-31T23:59:59Z",
  "reason": "aaaaaaaaaa",
  "status": "active",
  "scope": "prod"
}
```

Runtime results:

```text
receipt_validation (True, [])
gpl_policy (True, [])
```

It suppresses a deny-listed `GPL-3.0` component. The new regression proves only
that the four literal values from round 6 are denied; neighboring arbitrary
values still create an unauthoritative bypass receipt.

**Required:** do not activate an exemption from self-asserted display strings.
Resolve `approval_reference` to a durable authoritative record (or an
equivalent external authority lookup), and verify that the record binds the
named approver, role, package/advisory, scope, reason, issue/expiry timestamps,
and approval status. A pattern may pre-validate syntax, but cannot substitute
for resolution. Add the receipt above as a policy-level negative regression.

## Verification performed

```text
python3 -m pytest -q tests/security/test_supply_chain_security_gate.py
  50 passed
python3 -m pytest -q tests -k "sbom or license or security"
  exit 0
python3 scripts/security/generate_sbom.py --help
  exit 0
python3 -m ruff check scripts/security tests/security/test_supply_chain_security_gate.py
  clean
git diff --check
  clean before this review artifact
```

Additional review covered the complete remediation diff
`8c35410c..1a0287f4`, all three production implementations, their Round-7
negative tests, current exemption registries, and isolated fault injection for
nested scanner schemas, manifest integrity, and active exemption authority.

## Round-8 exit criteria

1. Required manifests cannot be absent, malformed, wrong-typed, or incomplete
   across either production or development dependency sections while SBOM
   generation succeeds.
2. Scanner reports with malformed or incomplete nested entries cannot be
   reported as clean, even with exit code zero.
3. An exemption becomes active only after its approval reference resolves to
   authoritative evidence bound to the receipt fields; the reproduced
   `Fake Person` receipt cannot suppress a denied license.
4. Focused tests, the new negative tests, CLI help, Ruff, and
   `git diff --check` pass at one new exact pushed head.

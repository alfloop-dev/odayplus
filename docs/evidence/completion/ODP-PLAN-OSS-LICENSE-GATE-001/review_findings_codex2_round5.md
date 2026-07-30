# ODP-PLAN-OSS-LICENSE-GATE-001 — Reviewer Findings (Codex2, round 5)

- Task: 建立 license-aware SBOM 與 OSS release gate
- Owner: Antigravity5
- Reviewer: Codex2
- Exact head reviewed: `e94f4b659a23843df35b471b32d7ad6404ebae9b`
- Previous reviewed head: `7c9340a0221fb2618493dbc77083bc408a840dd6`
- Verdict: **CHANGES_REQUESTED — task reopened, approval withheld**

The round-5 remediation correctly closes the previously reported bare-role,
inactive-exemption, placeholder-registry, and first-party PURL-prefix defects.
In particular, `Human/Ops` is now rejected, non-`active` entries are not used
for suppression, the vulnerability receipt is `review_required`, the license
registry is empty, and the spoofed first-party PURLs are rejected.

The exact task verification command passes at this head. Fault-injection review
nevertheless found three remaining fail-open paths in the release gate. These
are not theoretical style concerns: the return values reproduced below report
`PASS` after the underlying dependency inventory or scanner failed.

## Blocking findings

### B1 — unreadable dependency inventories produce a partial SBOM that passes policy

`generate_sbom.py:698-760` catches every `package-lock.json` parsing or schema
error, prints a warning, and continues. `generate_sbom.py:778-831` does the same
for `uv.lock`; a missing lockfile is also silently skipped. The resulting SBOM
can contain only the first-party root component, which is skipped by the
first-party policy rule, so `check_license_policy` returns `PASSED`.

Reproduction at the reviewed head, with a malformed `package-lock.json` and no
`uv.lock`:

```text
Warning: Failed to parse package-lock.json: Expecting property name enclosed in double quotes
malformed_lock_component_count= 1
malformed_lock_policy_result= (True, [])
```

This violates both "cataloging all runtime and build-time dependencies" and the
task's fail-closed acceptance criterion. A warning is not a gate result.

**Required:** make required lockfile absence, parse failure, or unsupported
schema fatal to generation/policy verification. Add negative tests proving that
malformed or incomplete Node and Python inventories cannot emit a passing SBOM.

### B2 — parseable scanner error payloads are accepted as clean audits

`vulnerability_scan.py:247-265` only rejects an `npm audit` non-zero exit when
stdout is empty or invalid JSON. A valid JSON error object has no
`vulnerabilities` key, so the code iterates an empty mapping and returns
`(True, [])`. The Python path at `vulnerability_scan.py:286-312` has the same
shape: a valid JSON error object has no `dependencies` key and returns
`(True, [])`.

Fault injection at this exact head:

```text
node_parseable_error_result= (True, [])
python_parseable_error_result= (True, [])
```

The mocked payloads were valid JSON error objects with return code 1, modeling
registry/index unavailability. Thus both declared deploy audit steps can say
PASS after performing no usable audit.

**Required:** validate the expected success/finding schema, explicitly reject
top-level scanner error payloads, and treat a non-zero exit as acceptable only
when a complete, recognized vulnerability report was parsed. Add Node and
Python negative tests for valid-JSON error responses and missing/malformed
report fields.

### B3 — the exemption "positive schema" validates field presence, not field values

Both loaders require keys, but neither validates the full positive schema:

- `issued_at` is never parsed or range-checked.
- `status` and `scope` are not checked against enumerations.
- `approval_reference` accepts any non-whitespace string, including `"x"`.
- `reason` can be empty.
- The license gate does not apply exemption `scope` at all.

An active license exemption with `scope: "dev"`, `issued_at: "not-a-date"`,
`approval_reference: "x"`, and a named-role-shaped approver suppressed a
deny-listed `GPL-3.0` component in the release policy check:

```text
dev_scoped_license_exemption_result= (True, [])
```

The analogous vulnerability entry loaded as active with no schema violations:

```text
invalid_issued_at_vulnerability_schema_result= ([{...}], [])
```

This leaves C4 only partially fixed: `status != active` is safely ignored, but
an invalid active receipt can still become normative.

**Required:** use one shared validator for both registries with explicit
enums, non-empty semantic fields, UTC timestamp parsing, temporal ordering, and
a resolvable approval-reference contract. Define and enforce license exemption
scope; a dev-only receipt must never suppress a production/release license
finding. Add negative tests for every invalid active-receipt field.

## Non-blocking documentation drift

`docs/security/LICENSE_AND_SUPPLY_CHAIN_POLICY.md` still advertises
`Human/Ops` as an acceptable approver at lines 37 and 60 even though the new
validator correctly rejects it. It also says every component must contain
`hashes` at line 17, contradicting the accepted C2 behavior that omits hashes
when no authentic artifact bytes exist. Update the policy together with the
blocking remediation so operators do not recreate an invalid receipt from the
canonical instructions.

## Verification performed

```text
pytest -q tests -k "sbom or license or security"
  254 selected; 247 passed, 7 skipped
python3 scripts/security/generate_sbom.py --help
  exit 0
git diff --check
  clean before this review artifact
```

Additional source review covered the task diff against `origin/dev`, the two
deploy workflows, `scripts/deploy_cloud_run_waji.sh`, both exemption
registries, the license policy, and the release readback/digest binding path.

## Round-6 exit criteria

1. Required dependency inventories fail closed on absence, parse error, and
   unsupported structure.
2. Node and Python scanner error payloads cannot be reported as clean audits.
3. Both exemption registries use the same value-validating schema; invalid
   active receipts never suppress findings, and license scope is enforced.
4. The security policy matches the enforced named approver, receipt schema,
   and authentic-hash behavior.
5. The focused suite, the new negative tests, CLI help, and `git diff --check`
   pass at one new exact pushed head.

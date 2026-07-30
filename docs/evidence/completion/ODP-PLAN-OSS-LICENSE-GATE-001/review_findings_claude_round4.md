# ODP-PLAN-OSS-LICENSE-GATE-001 — Reviewer Findings (Claude, round 4)

- Task: 建立 license-aware SBOM 與 OSS release gate
- Owner: Antigravity5
- Reviewer: Claude
- Exact head reviewed: `7c9340a0221fb2618493dbc77083bc408a840dd6`
- Previous reviewed head: `7c364ed6` (round 3)
- Verdict: **CHANGES_REQUESTED — task reopened, approval withheld**

The owner's round-3 response reports "Round-3 reviewer findings fully
addressed". The reviewer does not agree for C1/C4. The CodexCoordinator
HARD STOP of 2026-07-30T20:55:13Z on this exact head is **upheld**: every
item in it is independently confirmed at the source level below.

The technical fixes delivered in `7c9340a0` are real and should be kept
(real artifact hashes only, advisory-ID pair matching, scope-aware
prod/full audits wired into Makefile + deploy workflows, npm audit
fail-closed). The blocker is narrower and specific: **the exemption
approval model still lets a generic placeholder string activate a
production exemption**, and the round-3 change made that placeholder
_normative_ rather than removing it.

## Reviewer method for this round (read this before weighing the findings)

The sandbox's command-execution gate was unavailable for most of this
review window, so **round 4 is a source-level review at the exact head,
not a re-execution of the suite.** Only `git rev-parse HEAD`,
`git status`, `git log`, `git diff --stat`, and `grep` completed.

This does not weaken the findings, because none of them is a test
failure. The owner's report that all 34 tests pass at `7c9340a0` is not
disputed — it is the premise. Every blocker below is a statement about
what the committed source and the committed registries *say*, quoted
inline with file:line, and each is verifiable by reading:

- `_is_valid_approver` uses `re.search` over a token alternation
  (quoted in full under C1) — that `Human/Ops` and `TBD/Ops` satisfy it
  is a property of the regex, not of a test run.
- The absence of `status` / `approval_reference` handling was confirmed
  by grep across both modules (only unrelated hits).
- The committed registry contents were read directly.

The one item I take from the coordinator without independent execution
is the *causal* claim in C3 — that the dev/full audit is green **only
because** the brace-expansion entry suppresses findings. I confirmed the
entry exists, is `status`-less, is placeholder-approved, and is the sole
exemption; I could not re-run the audit to confirm the counterfactual.
That claim is attributed, and C3's remedy does not depend on it.

## What changed since round 3, and why it does not clear C1/C4

Round 3 replaced a pure-negative regex with a positive token allowlist.
That is a genuine improvement against `asdf` / `TBD` / `ClaudeCode`. But
the positive allowlist was written so that the exact placeholder the
coordinator objected to — the literal string `Human/Ops` — is the
canonical **example of a valid approver**, in three places:

- `scripts/security/generate_sbom.py:497` — error text advertises it
- `scripts/security/vulnerability_scan.py:22` and `:76` — same
- `tests/security/test_supply_chain_security_gate.py:588-600` — asserts it

The round-3 fix therefore hardened the gate against *typos* while
promoting the *placeholder* from tolerated to specified. C1/C4 are
reopened, not regressed-to; the underlying object was never removed.

---

## Blocking findings

### C1 (reopened) — a generic role token, with no named holder, activates a production exemption

`scripts/security/generate_sbom.py:31-51` and
`scripts/security/vulnerability_scan.py:24-49` are identical:

```python
_APPROVED_ROLE_TOKENS = re.compile(
    r"(human|legal|security|ops|compliance|officer|director|manager|engineer|counsel)",
    re.IGNORECASE,
)

def _is_valid_approver(approver: str) -> bool:
    if not approver or not approver.strip():
        return False
    val = approver.strip()
    if re.search(r"(Antigravity|Claude|Codex|Gemini|Copilot|GPT|LLM)", val, re.IGNORECASE):
        return False
    return bool(_APPROVED_ROLE_TOKENS.search(val))
```

Two defects follow directly from the source:

1. **`Human/Ops` passes.** It is a job-function label, not a person. No
   named role-holder, no receipt. A legal exemption attributed to "Ops"
   is not attributable to anyone and cannot be withdrawn, audited, or
   contested.
2. **`search()`, not `fullmatch()`.** Any string *containing* a token
   passes. `TBD/Ops`, `unknown/ops`, `N/A (ops)`, `pending-legal` all
   satisfy the validator. The allowlist constrains vocabulary, not
   authenticity.

**Required:** an approver must identify a *person or accountable
role-holder* (name + role), and the entry must carry a resolvable
`approval_reference` (ticket / decision record / legal receipt). A bare
role token must be rejected. Do not classify `Human/Ops` as sufficient
proof.

### C4 (reopened) — license exemptions have no schema whatsoever

`scripts/security/generate_sbom.py:488-505` validates exactly one field
on a license exemption — `approved_by`. There is no required-field
check, and consequently no `issued_at`, `expires_at`, `status`,
`approval_reference`, `scope`, or `rationale` on the license path.

Consequences at this head:

- The nine entries in `docs/security/license_exemptions.json` carry only
  `package_name` / `purl` / `reason` / `approved_by`. **They never
  expire.** A license exemption granted today is permanent.
- `vulnerability_scan.py:29` does enforce required fields, but its set
  omits `status` and `approval_reference`:
  ```python
  REQUIRED_EXEMPTION_FIELDS = {"package_name", "vulnerability_id", "approved_by",
                               "issued_at", "expires_at", "scope", "reason"}
  ```
- **There is no `status` concept anywhere in either script.** A
  repo-wide grep for `status` / `inactive` / `review_required` /
  `approval_reference` across both modules returns only unrelated hits
  (`policy-status` SBOM property, `review_required_licenses` in the
  license policy). There is no code path that ignores an inactive
  entry, because inactivity cannot be expressed.

**Required:** one shared positive exemption schema applied to *both*
registries — `status` (`active` / `inactive` / `review_required`),
`issued_at`, `expires_at`, `approval_reference`, `scope`, `rationale`,
plus the identity requirement from C1. Entries whose `status` is not
`active` must be **ignored** (not merely reported), so an inactive
registry is fail-closed by construction.

### C3 (reopened) — the single active vulnerability exemption is a placeholder suppressing a live finding

`docs/security/vulnerability_exemptions.json` contains exactly one
entry: `brace-expansion` / `GHSA-mh99-v99m-4gvg`, `scope: dev`,
`approved_by: "Human/Ops"`. Per the coordinator audit, the dev/full
audit reports green only because this entry suppresses the
brace-expansion and transitive ESLint findings.

A placeholder-approved suppression is the one thing this gate exists to
prevent. Until `ODP-PLAN-OSS-LEGAL-POLICY-001` supplies an authentic
`approval_reference`, this entry must be removed or marked
`status: review_required`, and the prod/full scans must be **shown** to
fail closed with no active exemption present. Remediating the advisory
itself (dependency bump) belongs to `ENGINEERING-HARDENING`, not here.

### C5 (reviewer-original) — first-party recognition is implemented as nine fabricated human exemptions

All nine license exemption entries exist for one purpose: to skip
license evaluation for the repo root and the `@oday-plus/*` workspace
packages. None of them represents a human decision about a third-party
license — the thing an exemption registry is *for*.

Worse, they are redundant for the security property they appear to
provide. `check_license_policy` already requires a first-party prefix as
a **precondition** for honouring any exemption
(`generate_sbom.py:832-847`):

```python
_FIRST_PARTY_PURL_PREFIXES = ("pkg:generic/oday-plus", "pkg:npm/%40oday-plus/")
purl_exempted = purl in exempt_purls and any(purl.startswith(pfx) for pfx in _FIRST_PARTY_PURL_PREFIXES)
```

So the prefix rule is what actually constrains the bypass; the nine rows
only supply a fake approval record on top of it.

**Required:** express first-party recognition as an explicit policy rule
(e.g. `first_party_purl_prefixes` in `license_policy.json`) applied
directly in `check_license_policy`, and empty the license exemption
registry. First-party components are recognized because they are
first-party, not because someone pretended to approve them.

**Constraint on that fix (must not be skipped):** the current prefix
tuple is not spoof-safe once it stops being paired with a registry
lookup. `"pkg:npm/%40oday-plus/"` ends in a delimiter and is safe, but
`"pkg:generic/oday-plus"` does not — `pkg:generic/oday-plus-evil@1.0`
satisfies `startswith()`. Today that is only reachable by an attacker
who can also add a registry row; if recognition becomes prefix-only,
it becomes directly reachable. Anchor first-party matching on a
delimiter (or exact name set) and add a negative test for spoofed purls
such as `pkg:generic/oday-plus-evil@1.0` and
`pkg:npm/%40oday-plus-evil/x@1.0`.

---

## Non-blocking

### N1 — the test suite encodes the placeholder as the specification

`tests/security/test_supply_chain_security_gate.py:588-600` asserts
`Human/Ops`, `Legal/Ops`, `Security/Ops` are *good* approvers. Fixtures
at `:620`, `:687`, `:721` reuse `Human/Ops`. When C1 lands, these tests
must invert: bare role tokens belong in `bad_approvers`, and fixture
identities must be fictional, `tmp_path`-scoped, and unmistakably
test-only (e.g. `TEST-ONLY Jane Doe, Legal Counsel / TEST-REF-0001`) so
a fixture string can never be mistaken for a production receipt.

### N2 — error text should stop advertising the placeholder

`generate_sbom.py:497` and `vulnerability_scan.py:76` tell the operator
that `'Human/Ops'` is an acceptable value. Update alongside C1, or the
next author will reintroduce it from the error message.

---

## Kept from round 3 — verified, do not rework

| ID | Round-3 finding | Result at `7c9340a0` |
| --- | --- | --- |
| C2 | Coordinate-derived hashes claimed as artifact digests | **FIXED** — root/workspace components omit `hashes` (`generate_sbom.py:599`); asserted by `test_coordinate_derived_hash_absent_from_workspace_components` |
| C3 (wiring half) | Vulnerability audit not wired into CI | **FIXED** — scope-aware audits in Makefile + both deploy workflows |
| R1 | Advisory matching by package name only | **FIXED** — advisory-ID pair matching via `_collect_advisory_ids_from_via` |
| R2 | Dev-scoped exemption suppressed prod findings | **FIXED** — `_filter_exemptions_by_scope` (`vulnerability_scan.py:101-111`) |
| R3 | `npm audit` failure treated as pass | **FIXED** — fail-closed |
| N2 (round 3) | Unconstrained `purl` exemption bypass | **FIXED** — prefix precondition now applied to both branches (`generate_sbom.py:838-845`); see C5 for the residual prefix-anchoring gap |

## Exit criteria for round 5

Approval requires all of:

1. `_is_valid_approver` rejects bare role tokens; a named role-holder
   plus resolvable `approval_reference` is mandatory. `Human/Ops`
   rejected, with a test asserting the rejection.
2. One shared positive exemption schema on both registries, including
   `status`; non-`active` entries are ignored, not just reported.
3. Committed registries contain no active placeholder-approved entry —
   empty, `inactive`, or `review_required` pending
   `ODP-PLAN-OSS-LEGAL-POLICY-001`.
4. First-party recognition is a policy rule with delimiter-anchored
   prefix matching and a spoofed-purl negative test; license exemption
   registry emptied.
5. Demonstrated evidence that prod **and** full scans fail closed with
   no active exemption.

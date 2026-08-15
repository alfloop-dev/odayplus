# Closeout metadata receipt

This evidence-only follow-up anchors the already merged dependency remediation
for PR #708. It changes no dependency, lockfile, SBOM, or audit policy; it
exists so the closeout gate has an approved commit subject carrying the task id.

- Task: `ODP-ENG-DEPENDENCY-REMEDIATION-001`
- Approved head: `f6f1ba1e6f908456b95963b18380cef231f23c71`
- Merge commit: `9d35a24015812dae8e4e9901d3d44fa3bc906987`
- PR: #708, merged into `dev` at 2026-08-10T11:53:33Z
- Merge-group CI: green (orchestrator, performance-gate, product,
  product-e2e-gate, task-review-gate)

## Delivered

- Node lockfile-only bumps: `brace-expansion` 1.1.16 -> 1.1.18 and 5.0.7 ->
  5.0.9, `js-yaml` 4.3.0 -> 4.3.1
- Python root lockfile-only bump: `gitpython` 3.1.55 -> 3.1.58
- `services/provider-gateway/requirements.txt`: `fastapi` 0.115.6 -> 0.138.1
  with an explicit `starlette>=1.3.1` security floor
- `docs/evidence/completion/ODP-PGAP-SUPPLY-001/sbom.json` regenerated to match
  the composed lockfiles

## Re-verified at the merged head

```
npm audit --omit=dev --audit-level=high   # found 0 vulnerabilities
npm audit                                 # found 0 vulnerabilities
uv run pytest tests/security/test_supply_chain_security_gate.py   # 13 passed
```

The supply-chain gate test re-derives the SBOM from the live `package-lock.json`
and `uv.lock` and asserts an exact component match, so SBOM/lockfile agreement is
enforced rather than asserted.

## Not closed — handed back for named human decision

`cryptography` 48.0.1 (PYSEC-2026-3552/3553/3554) is fix-only via a major bump
that `mlflow==3.14.0` blocks, and `pip-audit --local` audits zero packages under
`uv run --with`. Both are recorded in
[`../completion/ODP-ENG-DEPENDENCY-REMEDIATION-001/HUMAN_DECISION_HANDBACK.md`](../completion/ODP-ENG-DEPENDENCY-REMEDIATION-001/HUMAN_DECISION_HANDBACK.md).
No waiver, allowlist, or risk acceptance was signed by an AI agent.

## Closeout round 2 — commit trailer repair

Review round 1 reopened at `16680276cb455cfc3c493f26ed9fa4bb840dfe44`. That
commit's body was written with literal `\n` escape sequences instead of real
newlines, so `LLM-Agent`, `Task-ID`, `Reviewer`, and `Verified` collapsed onto
one line and were not standalone parseable trailers. The commit was already
published, so it was neither amended nor force-pushed; this narrow evidence-only
commit is appended on top with real newline-separated trailers so the closeout
gate reads a valid task-id-bearing HEAD. No dependency, lockfile, SBOM, or audit
policy content changed in either commit.

Base was composed forward to `origin/dev` at `273a7705` (pricing-simulation
lane, no overlap with this task's files; merge was conflict-free).

### Re-verified at the composed base

```
npm audit --omit=dev --audit-level=high                          # found 0 vulnerabilities
npm audit                                                        # found 0 vulnerabilities
uv run pytest tests/security/test_supply_chain_security_gate.py  # 13 passed
uv run --with pip-audit pip-audit -r services/provider-gateway/requirements.txt --no-deps
                                                                 # No known vulnerabilities found
```

The immutable handback record is unchanged: SHA256 over
`HUMAN_DECISION_HANDBACK.md` up to its digest line recomputes to
`sha256:ed6b3d5b1c412851b262e138df5d960af40d3fd6aa22e0e4eed87abdf65ad513`,
matching the digest recorded in the file.

# Human Decision Handback — ODP-ENG-DEPENDENCY-REMEDIATION-001

- Task: Remediate dependency findings not requiring risk acceptance
- Raised by: Claude (auto worker, owner)
- Reviewer: CodexCoordinator
- Raised at: 2026-08-08
- Anchored to commit: `888b6c077164a3775f6f5b7ef14b72e50662b692`
- Status: **OPEN — awaiting a named human decision**

> This record is deliberately unsigned. Every remaining item below requires a
> major version bump or a risk acceptance, and no AI agent in this task is
> permitted to sign a waiver or force a breaking upgrade on a human's behalf.
> Do not edit the decision record in place: to decide, append a new
> `## Decision` block and leave the original text and digest intact.

---

## Item 1 — `cryptography` 48.0.1 (production dependency)

### Findings

| Advisory | Summary | Fix version |
| --- | --- | --- |
| PYSEC-2026-3552 | `pkcs7_decrypt_der` / `pkcs7_decrypt_pem` / `pkcs7_decrypt_smime` leak the RSA-recovered length through distinguishable outcomes and timing — a Bleichenbacher oracle against the content-encryption key. Introduced in 44.0.0. | **50.0.0** |
| PYSEC-2026-3553 | Duplicate self-signed certificates in an invalid chain cause exponential blowup during path building; an attacker-controlled chain can take >5s to reject (resource-exhaustion DoS). | **49.0.0** |
| PYSEC-2026-3554 | A leaf wildcard SAN `*.example.com` is accepted under an intermediate name constraint permitting only `foo.example.com`, escaping the permitted subtree. | **49.0.0** |

### Why this is not remediable by this task

`pyproject.toml` declares `cryptography>=45,<49`. That ceiling is not merely a
local preference — it mirrors an upstream constraint:

```
mlflow==3.14.0 depends on cryptography>=43.0.0,<49
```

So every available fix is a **major** bump that also requires moving mlflow.
That is a risk acceptance, not a remediation, and is out of scope for an AI
worker under this task's rules.

### Resolver evidence (what is actually reachable today)

| Option | Resolves to | Advisories closed | Advisories left open |
| --- | --- | --- | --- |
| **A — status quo** | `mlflow 3.14.0`, `cryptography 48.0.1` | none | 3552, 3553, 3554 |
| **B — bump both, stay in-range for mlflow** (`mlflow>=3.14,<4` + `cryptography>=49`) | `mlflow 3.15.1`, `cryptography 49.0.0` | 3553, 3554 | 3552 |
| **C — chase 50.0.0** (`cryptography>=50`) | only satisfiable at `mlflow 3.2.0` | 3552, 3553, 3554 | none, but mlflow regresses ~13 minor versions |
| **D — wait for upstream** | mlflow relaxes its `<49`/`<50` ceiling | eventually all | all, until upstream ships |

Option B keeps mlflow inside the constraint the repo already declares
(`mlflow>=3.7,<4`) and would require editing exactly one line:
`cryptography>=45,<49` → `cryptography>=45,<50`, then `uv lock`.

### Exposure analysis (input to the decision, **not** a waiver)

A repository-wide search found **no call sites for the affected APIs**:

- No `pkcs7_decrypt_der` / `pkcs7_decrypt_pem` / `pkcs7_decrypt_smime` usage
  anywhere (PYSEC-2026-3552).
- No `PolicyBuilder`, `ClientVerifier`, `ServerVerifier`, or
  `verify_directly_issued_by` usage anywhere (PYSEC-2026-3553, -3554 are both
  in X.509 path verification).

Actual `cryptography` usage in this repository is limited to:

| Call site | Uses |
| --- | --- |
| `modules/opsboard/auth/jwks.py`, `modules/opsboard/auth/jwt.py` | RSA public-key deserialization and PKCS1v15 signature verification for JWT/JWKS |
| `modules/notifications/domain/authority.py` | Ed25519 public-key load and verify |
| `modules/avm/domain/outcome.py` | Ed25519 verify |

This narrows blast radius, but it does not close the findings, and the
judgement about whether that is acceptable belongs to a human.

### Decision required

Pick A, B, C, or D — and if the answer is A or D, record an explicit,
time-bounded, human-signed risk acceptance for PYSEC-2026-3552/3553/3554.

---

## Item 2 — the Python supply-chain gate audits zero packages

### Finding

`make dependency-audit` (`Makefile:48`) and
`tests/security/test_supply_chain_security_gate.py:42` both run:

```bash
uv run --with pip-audit pip-audit --local
```

Under `uv run --with`, pip-audit runs inside an ephemeral overlay environment.
`--local` then restricts the audit to that overlay's own site-packages, which
contains only pip-audit and its own dependencies. The project's dependencies
are never audited:

```bash
$ uv run --with pip-audit pip-audit --local --format=json   # dependencies audited: 0
$ uv run --with pip-audit pip-audit --format=json           # dependencies audited: 247
```

Every `No known vulnerabilities found` this gate has emitted for Python is
vacuous. The `gitpython` and `cryptography` findings in this task were only
visible once `--local` was dropped.

### Why this task did not simply fix it

The fix is one word — delete `--local` in both places. But the moment the gate
becomes real it fails on Item 1, because `cryptography 48.0.1` has no
non-major fix. The only ways to land the fix green today are:

1. decide Item 1 (option B still leaves PYSEC-2026-3552 open), or
2. add `--ignore-vuln PYSEC-2026-3552 …`, which is an AI-signed waiver and is
   forbidden by this task's brief.

Turning `dev` CI red repo-wide for every other lane is also well outside this
task's mandate. The fix is therefore prepared but not applied.

### Prepared fix (apply together with the Item 1 decision)

```diff
--- a/Makefile
+++ b/Makefile
-	$(UV) run --with pip-audit pip-audit --local
+	$(UV) run --with pip-audit pip-audit
```

```diff
--- a/tests/security/test_supply_chain_security_gate.py
+++ b/tests/security/test_supply_chain_security_gate.py
-        ["uv", "run", "--with", "pip-audit", "pip-audit", "--local"],
+        ["uv", "run", "--with", "pip-audit", "pip-audit"],
```

### Decision required

Approve the gate fix and schedule it with the Item 1 outcome, or explicitly
accept a Python audit that is known to check nothing.

---

## Item 3 — service requirements files are outside the gate

`make dependency-audit` never scans `infra/mlflow/requirements.txt` or
`services/provider-gateway/requirements.txt`. The 9 starlette advisories fixed
in this task (see `completion_evidence.md` §2.3) were invisible to CI and were
found only by auditing those files by hand:

```bash
uv run --with pip-audit pip-audit -r services/provider-gateway/requirements.txt --no-deps
uv run --with pip-audit pip-audit -r infra/mlflow/requirements.txt --no-deps
```

`infra/mlflow/requirements.txt` still resolves `cryptography==48.0.1`, for the
same upstream mlflow reason as Item 1; it holds no findings independent of it.

### Decision required

Whether to extend the dependency-audit target to cover per-service requirements
files, as a follow-up task.

---

## Immutability

The record above is fixed at the digest below. A decision is recorded by
appending a new `## Decision` section beneath this line, naming the human
approver, the option chosen, and the date — never by editing the text above.

- Record digest (SHA256 over this file's content preceding the digest line): `sha256:ed6b3d5b1c412851b262e138df5d960af40d3fd6aa22e0e4eed87abdf65ad513`
- Anchored commit: `888b6c077164a3775f6f5b7ef14b72e50662b692`
- AI signature: **none, by design**

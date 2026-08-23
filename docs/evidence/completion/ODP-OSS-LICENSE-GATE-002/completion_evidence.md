# ODP-OSS-LICENSE-GATE-002 Completion Evidence

## Summary
Implements a lock-bound, cross-repo OSS license and release gate with CycloneDX 1.5 SBOM generation, NOTICE reconciliation, fail-closed policy evaluation, signed attestation contract, and comprehensive positive and negative test suites.

## Deliverables
1. **CycloneDX 1.5 SBOM Generator (`delivery_toolchain/security/generate_sbom.py`)**:
   - Parses `package-lock.json` and `uv.lock` alongside installed distribution metadata.
   - Populates per-component `licenses`, `purl`, `supplier`/`author`, `hashes` (SHA-512 for npm integrity, SHA-256 for python wheels/sdist), and `scope` (`required` vs `optional`).
   - Emits CycloneDX dependency graph linking root and component purls.
   - Binds metadata properties: `git-sha`, `sbom-content-digest`, `container-base-images` (`python:3.12-slim`, `node:22-slim`), and `repository-release-digests` (`alfloop-dev/odayplus`, `alfloop-dev/pantheon`).
   - Supports `--check` and `--output` CLI flags.

2. **NOTICE Reconciliation and Gate Policy Evaluator (`delivery_toolchain/security/generate_oss_notice.py`)**:
   - Reconciles installed npm packages (451) and Python packages (236) against `docs/security/license_policy.json`.
   - Eliminates UNKNOWN classifications for declared Python packages by resolving PEP 639 `License-Expression`, clean `License`, `Classifiers`, and documented mappings.
   - Discharges standing obligations (Apache-2.0, CC-BY-4.0, MPL-2.0, LGPL disclosures).
   - Removed false claim that Human/Ops already approved LGPL handling; notes policy is proposed pending external authoritative receipt.
   - Implements fail-closed `evaluate_policy()` supporting compound expressions (OR / AND rules) and precedence order (`deny > review_required > allow_with_obligations > allow`).
   - Supports `--check` and `--reconcile` CLI flags.

3. **License Policy and Proposal Boundary (`docs/security/license_policy.json`, `docs/security/license_exemptions.json`)**:
   - `status` remains strictly `proposed` until external authoritative receipt is returned.
   - Updated `enforcement` section to document the active gate implementation.
   - Updated caniuse-lite `current_state` to reflect attribution present in `NOTICE-THIRD-PARTY.md`.

4. **Attestation Contract (`delivery_toolchain/security/attestation.py`, `docs/evidence/completion/ODP-OSS-LICENSE-GATE-002/license_gate_attestation.json`)**:
   - Binds release SHA, repository release digests, container base images, source/lock hashes (`pyproject.toml`, `uv.lock`, `package.json`, `package-lock.json`, `license_policy.json`, `license_exemptions.json`, `NOTICE-THIRD-PARTY.md`, `sbom.json`), gate summary, and content integrity hash.
   - Provides readback verification with `--check`.

5. **Test Suites (`tests/security/test_oss_license_gate.py`, `tests/security/test_oss_notice.py`)**:
   - 22 tests in `test_oss_license_gate.py` + 6 tests in `test_oss_notice.py`.
   - Negative tests reject:
     - Stale NOTICE
     - Partial installs
     - Hash drift in attestation evidence
     - Wrong/unapproved scope
     - Denied licenses (GPL, AGPL, SSPL, BUSL)
     - Unknown / missing licenses
     - Expired exemptions
     - Local / AI-only approval claims
     - Tampered attestation integrity hash

## Verification Evidence

```bash
$ uv run pytest tests/security/test_oss_license_gate.py tests/security/test_oss_notice.py -q
............................                                             [100%]
28 passed in 20.61s

$ uv run python delivery_toolchain/security/generate_oss_notice.py --check
NOTICE-THIRD-PARTY.md matches the installed dependency trees.

$ uv run python delivery_toolchain/security/generate_sbom.py --check
SBOM at docs/evidence/completion/ODP-PGAP-SUPPLY-001/sbom.json is valid and up to date.

$ uv run python delivery_toolchain/security/attestation.py --check
Attestation contract at docs/evidence/completion/ODP-OSS-LICENSE-GATE-002/license_gate_attestation.json verified successfully.
```

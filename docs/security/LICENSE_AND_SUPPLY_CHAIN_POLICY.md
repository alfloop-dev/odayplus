# OSS License & Supply Chain Security Policy

## 1. Overview

This document specifies the software bill of materials (SBOM) standards, open-source software (OSS) license governance policy, image/release attestation binding rules, and dependency vulnerability audit requirements for Oday Plus (`pantheon` platform).

## 2. CycloneDX 1.5 SBOM Standard

Every release candidate and build must produce a CycloneDX 1.5 compliant JSON Software Bill of Materials (SBOM) cataloging all runtime and build-time dependencies across Node (`package-lock.json`) and Python (`uv.lock`).

### Required SBOM Metadata & Schema Fields:
- **Format**: CycloneDX 1.5 JSON (`"bomFormat": "CycloneDX"`, `"specVersion": "1.5"`).
- **Component Attributes**: Each cataloged package must include:
  - `name`, `version`, `purl` (Package URL), `bom-ref`.
  - `supplier`: Component distribution channel (`npm`, `pypi`).
  - `licenses`: SPDX license identifiers or declared license names.
  - `hashes`: Package integrity hashes (e.g., SHA-256 or SHA-512) for published packages with authentic artifact bytes; omitted when no authentic artifact bytes exist (such as for local workspace package links).
- **Dependency Graph Scope**: Top-level `dependencies` array detailing explicit package relationships (`ref` -> `dependsOn`).
- **Release Attestation Properties**:
  - `git-sha`: Exact Git commit SHA.
  - `sbom-hash`: Deterministic SHA-256 hash of component manifest.
  - `sbom-content-digest`: Unique `sha256:...` digest binding SHA and SBOM hash.
  - `image-digest`: OCI/Docker container image digest (e.g., `sha256:...`).
  - `release-digest`: Signed release attestation digest.
  - `policy-status`: License policy evaluation verdict (`PASSED` / `FAILED`).

## 3. OSS License Governance Policy

### License Classification:
- **Allowed Licenses**: Pre-approved permissive and open-source licenses:
  - `MIT`, `MIT-0`, `Apache-2.0`, `BSD-2-Clause`, `BSD-3-Clause`, `ISC`, `Python-2.0`, `MPL-2.0`, `CC0-1.0`, `CC-BY-4.0`, `BlueOak-1.0.0`, `Unlicense`, `LGPL-3.0-or-later`, `LGPL-3.0-only`, `LGPL-3.0`, `LGPL-2.1-or-later`, `Zlib`, `0BSD`, `PostgreSQL`, `POSTGRESQL`, `PSF-2.0`, `ZPL-2.1`, `TCL`, `CNRI-Python`.
- **Denied Licenses**: Strong copyleft or restrictive commercial licenses strictly prohibited in production builds without explicit legal review and exemption:
  - `GPL-3.0`, `GPL-3.0-only`, `GPL-3.0-or-later`, `AGPL-3.0`, `AGPL-3.0-only`, `AGPL-3.0-or-later`, `SSPL-1.0`, `BSL-1.1`.
- **LGPL Decision**:
  - Weak copyleft licenses `LGPL-2.1` and `LGPL-3.0` variants (`LGPL-2.1-or-later`, `LGPL-3.0`, `LGPL-3.0-only`, `LGPL-3.0-or-later`) are explicitly allowed outright, while strong copyleft (`GPL-3.0`, `AGPL-3.0`) remains denied pending legal review.
- **Review Required / Unclassified**:
  - Requires named exemption entry in `docs/security/license_exemptions.json` specifying package name, purl, reason, and approving authority. Approving authorities must be named human/legal authorities (e.g., `"Jane Doe (Legal Counsel)"`). AI agent names, bare role tokens (such as `Human/Ops` or `Legal/Ops`), and placeholder strings cannot serve as legal approvers.

### Fail-Closed Enforcement:
The release gate script `scripts/security/generate_sbom.py --check-policy` automatically parses all cataloged dependencies and verifies them against `docs/security/license_policy.json` and `docs/security/license_exemptions.json`.
If any unapproved or denied license is encountered without an explicit human-approved exemption, the gate **fails closed** (exit code 1) and aborts CI/release.

## 4. Attestation Readback & Verification

To verify and read back committed SBOM provenance and image/release digests:
```bash
python3 scripts/security/generate_sbom.py --readback
```
To verify committed SBOM freshness against live lockfiles:
```bash
python3 scripts/security/generate_sbom.py --verify
```

## 5. Vulnerability Audit Gates

Both production and development dependencies are audited continuously across both runtime ecosystems:
- **Node Full & Prod Audit**: `npm audit --audit-level=high` (full/dev) and `npm audit --omit=dev --audit-level=high` (prod).
- **Python Audit**: `uv run --with pip-audit pip-audit --local` (or `python3 scripts/security/vulnerability_scan.py`).

Any `HIGH` or `CRITICAL` vulnerability finding must either be remediated immediately by package version upgrade or documented with an authoritative, non-expired risk receipt in `docs/security/vulnerability_exemptions.json` approved by a named human/legal authority (e.g., `"Jane Doe (Legal Counsel)"`) before code promotion. AI names, bare role tokens (such as `Human/Ops` or `Legal/Ops`), and placeholder strings cannot serve as risk exemption approvers.

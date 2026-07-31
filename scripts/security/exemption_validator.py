"""Shared exemption schema and receipt validation for OSS license and vulnerability gates."""

import hashlib
import hmac
import json
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]

VALID_STATUSES = {"active", "inactive", "review_required"}
VALID_SCOPES = {"prod", "dev", "all", "production"}

# Disallowed approver patterns: AI names, bare roles, placeholders, short strings, fake names
REJECTED_APPROVER_EXACT = {
    "human/ops", "legal/ops", "security/ops", "tbd/ops", "unknown/ops",
    "n/a (ops)", "pending-legal", "ops", "legal", "security", "tbd", "n/a",
    "unknown", "none", "null", "undefined", "claude", "claudecode", "gpt",
    "gpt-4", "gemini", "antigravity", "codex", "copilot", "antigravity5",
    "codex2", "codex5", "codex6", "codex8", "codex9", "codexcoordinator",
    "antigravity2", "antigravity3", "antigravity4", "antigravity6", "antigravity7",
    "zzzzz", "fake", "fake person", "attacker", "attacker person",
    "test", "dummy", "placeholder",
}

BAD_NAME_TOKENS = {
    "fake", "attacker", "placeholder", "dummy", "zzzzz", "claude",
    "gpt", "gemini", "antigravity", "codex", "copilot", "tbd", "n/a",
    "unknown",
}

RECOGNIZED_ROLES = {
    "legal counsel", "general counsel", "counsel", "security officer",
    "chief information security officer", "ciso", "compliance director",
    "compliance officer", "head of legal", "legal lead", "security lead",
    "security director", "risk lead", "vp of legal", "director of security",
    "dpo", "data protection officer", "legal & security counsel",
    "legal", "security", "operations officer", "operations lead", "operations",
}

TRIVIAL_REF_VALUES = {
    "x", "1", "123", "tbd", "n/a", "none", "null", "undefined", "todo", "test", "a", "b", "c", "foo", "bar"
}

TRIVIAL_REASON_VALUES = {
    "x", "1", "123", "tbd", "n/a", "none", "null", "undefined", "todo", "test", "a", "b", "c", "foo", "bar", "temp", "tmp"
}

HEX_HASH_PATTERN = re.compile(r"^[a-fA-F0-9]{32,128}$")


def compute_file_sha256(file_path: Path) -> str:
    """Compute sha256 hex digest of a file if it exists, or 'MISSING'."""
    if not file_path.exists():
        return "MISSING"
    return hashlib.sha256(file_path.read_bytes()).hexdigest()


def compute_policy_hash(policy_path: Path | None = None) -> str:
    """Compute sha256 hex digest of license_policy.json."""
    path = policy_path or (ROOT / "docs/security/license_policy.json")
    return compute_file_sha256(path)


def compute_canonical_receipt_hash(receipt_data: dict) -> str:
    """Compute sha256 hex digest of canonical receipt payload excluding canonical_receipt_hash and signature."""
    payload = {
        k: v for k, v in receipt_data.items()
        if k not in {"canonical_receipt_hash", "signature"}
    }
    canonical_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def compute_receipt_signature(canonical_hash: str, secret_key: str) -> str:
    """Compute HMAC-SHA256 signature of canonical_receipt_hash using secret_key."""
    return hmac.new(secret_key.encode("utf-8"), canonical_hash.encode("utf-8"), hashlib.sha256).hexdigest()


REQUIRED_DIGEST_KEYS = {
    "source_digest",
    "release_digest",
    "sbom_digest",
    "evidence_report_digest",
}

ALLOWLISTED_VAULT_PATHS = {
    Path("/etc/pantheon/security").resolve(),
    Path("/var/lib/pantheon/vault").resolve(),
    Path("/etc/security/vault").resolve(),
    Path("/var/run/secrets/pantheon/vault").resolve(),
}


def validate_and_load_authority_key(
    key_file_path: Path | str | None,
    allow_test_vault: bool = False,
) -> tuple[str | None, str | None]:
    """Validate key file path, ownership, mode, symlink status, and vault provenance.

    Returns:
        (key_string, error_message)
    """
    if not key_file_path:
        return None, "No authority key file path provided."

    kf = Path(key_file_path)

    # 1. Symlink check (fail closed on symlinks)
    if kf.is_symlink():
        return None, f"Authority key file '{key_file_path}' is a symlink (symlink key files forbidden)."

    if not kf.exists():
        return None, f"Authority key file '{key_file_path}' does not exist on disk."

    if not kf.is_file():
        return None, f"Authority key file '{key_file_path}' is not a regular file."

    resolved = kf.resolve()

    # 2. Repository-local secret prohibition check for production
    try:
        resolved.relative_to(ROOT)
        if not allow_test_vault:
            return (
                None,
                f"Authority key file '{key_file_path}' (resolved: '{resolved}') is located inside repository root '{ROOT}'. "
                "Repository-local authority secrets are strictly forbidden in production.",
            )
    except ValueError:
        pass

    # 3. Vault path allowlist check (fail closed on arbitrary env/caller paths in production)
    is_allowlisted = False
    if allow_test_vault:
        is_allowlisted = True
    else:
        for vp in ALLOWLISTED_VAULT_PATHS:
            if resolved == vp or vp in resolved.parents:
                is_allowlisted = True
                break

    if not is_allowlisted:
        return (
            None,
            f"Authority key file '{key_file_path}' (resolved: '{resolved}') is not located in an allowlisted vault directory. "
            "Arbitrary caller-selected key files and temporary paths are rejected in production.",
        )

    # 4. Restrictive mode permissions check (must be 0o600 or 0o400; no group or world access)
    st = resolved.stat()
    mode_bits = st.st_mode & 0o077
    if mode_bits != 0:
        return (
            None,
            f"Authority key file '{resolved}' has unrestrictive permissions 0o{st.st_mode & 0o777:o}. "
            "Key file must have restrictive permissions 0o600 or 0o400 with no group or world access.",
        )

    # 5. Owner check (must be current process UID or root UID 0)
    current_uid = os.getuid()
    if st.st_uid != current_uid and st.st_uid != 0:
        return (
            None,
            f"Authority key file '{resolved}' uid {st.st_uid} does not match process uid {current_uid} or root.",
        )

    # 6. Parent directory permissions check (must not be world-writable)
    parent_st = resolved.parent.stat()
    if (parent_st.st_mode & 0o002) != 0:
        return (
            None,
            f"Parent directory '{resolved.parent}' of authority key file is world-writable.",
        )

    raw_key = resolved.read_text(encoding="utf-8").strip()
    if not raw_key or len(raw_key) < 16:
        return None, f"Authority key file '{resolved}' contains empty or insufficient key length (min 16 chars)."

    return raw_key, None


class AuthoritativeReceiptVerifier:
    """Concrete verifier that obtains and validates authoritative source-system readback and signature data."""

    def __init__(
        self,
        authority_key_file: Path | str | None = None,
        authority_manifest_path: Path | str | None = None,
        trusted_source_systems: set[str] | None = None,
        expected_policy_version: str | None = None,
        expected_digests: dict[str, str] | None = None,
        allow_test_vault: bool = False,
    ):
        self.authority_key: str | None = None
        self.key_error: str | None = None
        self.allow_test_vault = allow_test_vault

        # B1 fix: Resolve authority key from external allowlisted vault path only.
        target_key_path = authority_key_file or os.getenv("OSS_LEGAL_AUTHORITY_KEY_FILE")
        if not target_key_path:
            for external_default in [
                Path("/etc/pantheon/security/legal_authority.key"),
                Path("/var/lib/pantheon/vault/legal_authority.key"),
                Path("/etc/security/vault/legal_authority.key"),
            ]:
                if external_default.exists():
                    target_key_path = external_default
                    break

        if target_key_path:
            self.authority_key, self.key_error = validate_and_load_authority_key(
                target_key_path, allow_test_vault=allow_test_vault
            )
        else:
            self.key_error = "No authority key file path configured or found in external allowlisted vault."

        self.trusted_source_systems = trusted_source_systems or {
            "ODP-PLAN-OSS-LEGAL-POLICY-001",
            "https://governance.pantheon.internal/policy",
            "legal_vault",
        }
        self.expected_policy_version = expected_policy_version or "1.0.0"

        # B2 fix: Mandatory independent expected digests MUST be obtained from an authenticated, signed authority manifest.
        self.expected_digests: dict[str, str] = {}
        self.manifest_error: str | None = None
        self.has_complete_expected_digests = False

        if self.authority_key:
            target_manifest_path = authority_manifest_path or os.getenv("OSS_LEGAL_AUTHORITY_MANIFEST_PATH")
            if not target_manifest_path:
                for external_manifest in [
                    Path("/etc/pantheon/security/authority_manifest.json"),
                    Path("/var/lib/pantheon/vault/authority_manifest.json"),
                    Path("/etc/security/vault/authority_manifest.json"),
                ]:
                    if external_manifest.exists():
                        target_manifest_path = external_manifest
                        break

            if target_manifest_path and Path(target_manifest_path).exists():
                m_ok, m_digests, m_err = self._load_and_verify_manifest(Path(target_manifest_path))
                if m_ok and m_digests:
                    if expected_digests:
                        for k, v in expected_digests.items():
                            if m_digests.get(k) != v:
                                self.manifest_error = f"Caller-supplied expected digest '{k}' does not match signed authority manifest."
                                m_ok = False
                                break
                    if m_ok:
                        self.expected_digests = m_digests
                        self.has_complete_expected_digests = True
                else:
                    self.manifest_error = f"Failed to authenticate authority manifest: {m_err}"
            else:
                self.manifest_error = f"Authority manifest file missing at {target_manifest_path or 'unconfigured external vault path'}"
        else:
            self.manifest_error = self.key_error or "No valid authority key to verify authority manifest."

        if self.has_complete_expected_digests:
            missing_dig_keys = REQUIRED_DIGEST_KEYS - set(self.expected_digests.keys())
            if missing_dig_keys or any(not self.expected_digests.get(k) for k in REQUIRED_DIGEST_KEYS):
                self.has_complete_expected_digests = False

    def _load_and_verify_manifest(self, manifest_path: Path) -> tuple[bool, dict[str, str] | None, str | None]:
        if manifest_path.is_symlink():
            return False, None, f"Authority manifest file '{manifest_path}' is a symlink (symlinks forbidden)."
        if not self.allow_test_vault:
            try:
                manifest_path.resolve().relative_to(ROOT)
                return False, None, f"Authority manifest file '{manifest_path}' is inside repository root (repository-local secrets forbidden in production)."
            except ValueError:
                pass
        try:
            m_data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as e:
            return False, None, f"Failed to parse manifest JSON: {e}"

        payload = {k: v for k, v in m_data.items() if k not in {"canonical_manifest_hash", "signature"}}
        canon_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        computed_hash = hashlib.sha256(canon_json.encode("utf-8")).hexdigest()
        actual_hash = m_data.get("canonical_manifest_hash", "")
        actual_sig = m_data.get("signature", "")

        if computed_hash != actual_hash:
            return False, None, f"Manifest hash mismatch: computed='{computed_hash}', actual='{actual_hash}'"

        expected_sig = compute_receipt_signature(computed_hash, self.authority_key)
        if not actual_sig or not hmac.compare_digest(actual_sig, expected_sig):
            return False, None, "Manifest signature mismatch against authority key."

        exp_digs = m_data.get("expected_digests", {})
        if not isinstance(exp_digs, dict):
            return False, None, "Manifest expected_digests is not an object schema."

        return True, exp_digs, None

    def verify(self, ref_str: str, receipt_data: dict, entry: dict) -> tuple[bool, str | None]:
        if not self.authority_key:
            return (
                False,
                f"Authoritative verifier has no valid authority key configured from allowlisted vault for receipt '{ref_str}': {self.key_error}",
            )

        if not self.has_complete_expected_digests:
            return (
                False,
                f"Authoritative verifier for '{ref_str}' has missing or unauthenticated expected digests: {self.manifest_error or 'incomplete digests'}",
            )

        src_sys = receipt_data.get("source_system", "")
        if src_sys not in self.trusted_source_systems:
            return (
                False,
                f"Authoritative receipt for '{ref_str}' source_system '{src_sys}' is not in trusted source systems "
                "(exact match required; substring matching forbidden).",
            )

        canon_hash = compute_canonical_receipt_hash(receipt_data)
        expected_sig = compute_receipt_signature(canon_hash, self.authority_key)
        actual_sig = receipt_data.get("signature", "")

        if not actual_sig or not hmac.compare_digest(actual_sig, expected_sig):
            return (
                False,
                f"Signature verification failed for receipt '{ref_str}': signature mismatch against configured authority key.",
            )

        return True, None


def is_valid_approver(approver: str) -> bool:
    """Validate that the approver is a named human/legal authority and not a bare role, AI agent, or placeholder token."""
    if not approver or not isinstance(approver, str):
        return False
    app_str = approver.strip()
    app_lower = app_str.lower()
    if not app_lower or len(app_lower) < 6:
        return False
    if app_lower in REJECTED_APPROVER_EXACT:
        return False

    for pattern in [
        r"\bhuman/ops\b", r"\blegal/ops\b", r"\bsecurity/ops\b", r"\btbd/ops\b",
        r"\bclaude\b", r"\bgpt\b", r"\bgemini\b", r"\bantigravity\b", r"\bcodex\b", r"\bcopilot\b",
        r"^\s*(ops|legal|security|tbd|n/a|pending|unknown|none|null|zzzzz|fake|attacker|dummy)\s*$",
    ]:
        if re.search(pattern, app_lower):
            return False

    role_pattern = r"^([A-Za-z0-9\.\-']+(?:\s+[A-Za-z0-9\.\-']+)+)\s*[\(\<\,\-\[]\s*([A-Za-z0-9\s/_\-\.\,\&]{3,})[\)\>\-\]]?$"
    match = re.match(role_pattern, app_str)
    if not match:
        return False

    name_part, role_part = match.group(1).lower().strip(), match.group(2).lower().strip()
    if name_part in REJECTED_APPROVER_EXACT:
        return False

    for bad in BAD_NAME_TOKENS:
        if bad in name_part:
            return False

    is_rec_role = any(r in role_part for r in RECOGNIZED_ROLES)
    if not is_rec_role:
        return False

    return True


def is_valid_approval_reference(ref: str) -> bool:
    """Validate that approval_reference is a non-empty, non-trivial reference string format."""
    if not ref or not isinstance(ref, str):
        return False
    ref_str = ref.strip()
    if not ref_str or len(ref_str) < 6:
        return False
    if ref_str.lower() in TRIVIAL_REF_VALUES:
        return False
    ref_pattern = r"^(PR-?\d+|ISSUE-?\d+|SEC-\d+|LEGAL-\d+|ADR-\d+|POLICY-[A-Z0-9_-]+|[A-Z0-9]+-[A-Z0-9_-]+)$"
    if not re.match(ref_pattern, ref_str, re.IGNORECASE):
        return False
    return True


def resolve_approval_reference(
    ref: str,
    entry: dict,
    base_dir: Path | None = None,
    verifier_fn: Any | None = None,
) -> tuple[bool, str | None]:
    """Resolve an approval_reference to an authoritative legal receipt record under ODP-PLAN-OSS-LEGAL-POLICY-001.

    Returns:
        (is_resolved, error_message)
    """
    if not ref or not isinstance(ref, str):
        return False, "Approval reference is missing or not a string."

    ref_str = ref.strip()
    if not is_valid_approval_reference(ref_str):
        return False, f"Approval reference '{ref_str}' has invalid or trivial reference format."

    search_dirs = []
    if base_dir:
        search_dirs.append(base_dir / "receipts")
        search_dirs.append(base_dir)
    search_dirs.append(ROOT / "docs/security/receipts")

    receipt_data = None
    receipt_source = None

    for sdir in search_dirs:
        rfile = sdir / f"{ref_str}.json"
        if rfile.exists():
            try:
                receipt_data = json.loads(rfile.read_text(encoding="utf-8"))
                receipt_source = str(rfile)
                break
            except Exception as e:
                return False, f"Authoritative receipt file '{rfile}' is malformed: {e}"

    if receipt_data is None:
        legal_file = ROOT / "docs/security/legal_policy_receipts.json"
        if legal_file.exists():
            try:
                fdata = json.loads(legal_file.read_text(encoding="utf-8"))
                receipts = fdata.get("receipts", {})
                if isinstance(receipts, dict) and ref_str in receipts:
                    receipt_data = receipts[ref_str]
                    receipt_source = str(legal_file)
            except Exception as e:
                return False, f"Legal policy receipts file '{legal_file}' is malformed: {e}"

    if receipt_data is None:
        return (
            False,
            f"Approval reference '{ref_str}' could not be resolved to an authoritative legal receipt record under ODP-PLAN-OSS-LEGAL-POLICY-001.",
        )

    if not isinstance(receipt_data, dict):
        return False, f"Authoritative receipt for '{ref_str}' in {receipt_source} is not an object schema."

    # B1 rule: A repository-local lookalike file CANNOT self-establish authority without a configured AuthoritativeReceiptVerifier with authority_key
    if verifier_fn is None or not isinstance(verifier_fn, AuthoritativeReceiptVerifier) or not verifier_fn.authority_key:
        return (
            False,
            f"Repository-local receipt '{ref_str}' cannot self-establish authority without a configured AuthoritativeReceiptVerifier instance and authority key under ODP-PLAN-OSS-LEGAL-POLICY-001.",
        )

    # B2 rule: Validate complete required authoritative receipt field set
    req_fields = {
        "approval_ref",
        "principal_id",
        "principal_role",
        "approved_by",
        "source_system",
        "policy_decision",
        "policy_name",
        "policy_version",
        "policy_hash",
        "scope",
        "issued_at",
        "reviewed_at",
        "expires_at",
        "source_digest",
        "release_digest",
        "sbom_digest",
        "python_lock_digest",
        "npm_lock_digest",
        "evidence_report_digest",
        "canonical_receipt_hash",
        "signature",
    }
    # package_name or purl required
    if not receipt_data.get("package_name") and not receipt_data.get("package_purl") and not receipt_data.get("purl"):
        req_fields.add("package_name")

    # vulnerability_id required if entry is vulnerability exemption
    if entry.get("vulnerability_id"):
        req_fields.add("vulnerability_id")

    missing = [f for f in req_fields if not receipt_data.get(f) or not isinstance(receipt_data.get(f), str)]
    if missing:
        return False, f"Authoritative receipt for '{ref_str}' is missing required fields: {sorted(missing)}"

    # Match approval reference
    rec_ref = receipt_data.get("approval_ref")
    entry_ref = entry.get("approval_reference")
    if rec_ref != ref_str or rec_ref != entry_ref:
        return False, f"Authoritative receipt for '{ref_str}' approval_ref '{rec_ref}' does not match entry approval_reference '{entry_ref}'."

    # Verify principal role authorization
    rec_role = receipt_data.get("principal_role", "").lower().strip()
    if not any(r in rec_role for r in RECOGNIZED_ROLES):
        return False, f"Authoritative receipt for '{ref_str}' contains unauthorized principal_role '{receipt_data.get('principal_role')}'."

    # Verify policy decision & status
    p_decision = receipt_data.get("policy_decision")
    if p_decision not in {"approved", "active", "waived_with_conditions"}:
        return False, f"Authoritative receipt for '{ref_str}' policy_decision '{p_decision}' is not approved."

    p_name = receipt_data.get("policy_name")
    if p_name != "ODP-PLAN-OSS-LEGAL-POLICY-001":
        return False, f"Authoritative receipt for '{ref_str}' policy_name '{p_name}' does not match 'ODP-PLAN-OSS-LEGAL-POLICY-001'."

    # B2 rule: Compare exact policy version with current governed policy version
    rec_pol_ver = receipt_data.get("policy_version")
    expected_pol_ver = getattr(verifier_fn, "expected_policy_version", None) or "1.0.0"
    if not rec_pol_ver or rec_pol_ver != expected_pol_ver:
        return (
            False,
            f"Authoritative receipt for '{ref_str}' policy_version '{rec_pol_ver}' does not match expected governed policy version '{expected_pol_ver}'.",
        )

    # Check policy content hash
    expected_pol_hash = compute_policy_hash()
    rec_pol_hash = receipt_data.get("policy_hash", "")
    if not HEX_HASH_PATTERN.match(rec_pol_hash):
        return False, f"Authoritative receipt for '{ref_str}' policy_hash '{rec_pol_hash}' is not a valid hex digest format."
    if expected_pol_hash and rec_pol_hash != expected_pol_hash:
        return False, f"Authoritative receipt for '{ref_str}' policy_hash '{rec_pol_hash}' does not match current policy hash '{expected_pol_hash}'."

    # Verify approver
    rec_approver = receipt_data.get("approved_by")
    entry_approver = entry.get("approved_by")
    if rec_approver != entry_approver:
        return False, f"Authoritative receipt for '{ref_str}' approved_by '{rec_approver}' does not match entry approved_by '{entry_approver}'."

    if not is_valid_approver(rec_approver):
        return False, f"Authoritative receipt for '{ref_str}' contains invalid approver '{rec_approver}'."

    # B2 rule: Verify package name and require exact normalized purl identity
    entry_pkg = entry.get("package_name")
    rec_pkg = receipt_data.get("package_name")
    if rec_pkg and entry_pkg and rec_pkg != entry_pkg:
        return False, f"Authoritative receipt for '{ref_str}' package_name '{rec_pkg}' does not match entry package_name '{entry_pkg}'."

    entry_purl = entry.get("purl") or entry.get("package_purl")
    rec_purl = receipt_data.get("package_purl") or receipt_data.get("purl")
    if entry_pkg or entry_purl or rec_purl:
        if not rec_purl or not entry_purl:
            return False, f"Authoritative receipt for '{ref_str}' requires exact package_purl binding, but purl is missing (entry purl: '{entry_purl}', receipt purl: '{rec_purl}')."
        if rec_purl.strip() != entry_purl.strip():
            return False, f"Authoritative receipt for '{ref_str}' package_purl '{rec_purl}' does not match entry purl '{entry_purl}'."

    # B2 rule: Verify source, release, sbom, and evidence-report digests against mandatory expected hex hashes
    exp_digests = getattr(verifier_fn, "expected_digests", None) or {}
    for d_key in ["source_digest", "release_digest", "sbom_digest", "evidence_report_digest"]:
        rec_val = receipt_data.get(d_key, "")
        if not rec_val or rec_val == "UNBOUND" or rec_val == "caller-controlled" or not HEX_HASH_PATTERN.match(rec_val):
            return False, f"Authoritative receipt for '{ref_str}' {d_key} '{rec_val}' is invalid, unbound, or caller-controlled."
        exp_val = exp_digests.get(d_key)
        if not exp_val or rec_val != exp_val:
            return False, f"Authoritative receipt for '{ref_str}' {d_key} '{rec_val}' does not match mandatory expected digest '{exp_val}'."

    # Verify vulnerability ID
    entry_vid = entry.get("vulnerability_id")
    rec_vid = receipt_data.get("vulnerability_id")
    if entry_vid or rec_vid:
        if rec_vid != entry_vid:
            return False, f"Authoritative receipt for '{ref_str}' vulnerability_id '{rec_vid}' does not match entry vulnerability_id '{entry_vid}'."

    # Verify scope
    entry_scope = entry.get("scope")
    rec_scope = receipt_data.get("scope")
    if rec_scope != entry_scope:
        return False, f"Authoritative receipt for '{ref_str}' scope '{rec_scope}' does not match entry scope '{entry_scope}'."

    # Verify lockfile digests
    uv_lock_path = ROOT / "uv.lock"
    pkg_lock_path = ROOT / "package-lock.json"
    if uv_lock_path.exists():
        expected_uv_hash = compute_file_sha256(uv_lock_path)
        rec_uv_hash = receipt_data.get("python_lock_digest", "")
        if rec_uv_hash != expected_uv_hash:
            return False, f"Authoritative receipt for '{ref_str}' python_lock_digest '{rec_uv_hash}' does not match uv.lock hash '{expected_uv_hash}'."

    if pkg_lock_path.exists():
        expected_npm_hash = compute_file_sha256(pkg_lock_path)
        rec_npm_hash = receipt_data.get("npm_lock_digest", "")
        if rec_npm_hash != expected_npm_hash:
            return False, f"Authoritative receipt for '{ref_str}' npm_lock_digest '{rec_npm_hash}' does not match package-lock.json hash '{expected_npm_hash}'."

    # Verify timestamps, ordering, and UTC format
    issued_at = receipt_data.get("issued_at", "")
    expires_at = receipt_data.get("expires_at", "")
    reviewed_at = receipt_data.get("reviewed_at", "")
    entry_issued = entry.get("issued_at", "")
    entry_expires = entry.get("expires_at", "")

    if issued_at != entry_issued:
        return False, f"Authoritative receipt for '{ref_str}' issued_at '{issued_at}' does not match entry issued_at '{entry_issued}'."
    if expires_at != entry_expires:
        return False, f"Authoritative receipt for '{ref_str}' expires_at '{expires_at}' does not match entry expires_at '{entry_expires}'."

    for ts_name, ts_val in [("issued_at", issued_at), ("expires_at", expires_at), ("reviewed_at", reviewed_at)]:
        if not (ts_val.endswith("Z") or ts_val.endswith("+00:00") or ts_val.endswith("+0000")):
            return False, f"Authoritative receipt for '{ref_str}' {ts_name} timestamp '{ts_val}' must be ISO UTC."

    try:
        issued_dt = datetime.fromisoformat(issued_at.replace("Z", "+00:00"))
        reviewed_dt = datetime.fromisoformat(reviewed_at.replace("Z", "+00:00"))
        expires_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        now_utc = datetime.now(UTC)

        if not (issued_dt <= reviewed_dt <= expires_dt):
            return (
                False,
                f"Authoritative receipt for '{ref_str}' timestamp ordering violation: expected issued_at ({issued_at}) <= reviewed_at ({reviewed_at}) <= expires_at ({expires_at}).",
            )

        if expires_dt <= now_utc:
            return False, f"Authoritative receipt for '{ref_str}' expired at {expires_at}."

        if issued_dt > now_utc + timedelta(minutes=5):
            return False, f"Authoritative receipt for '{ref_str}' has future issued_at timestamp {issued_at}."
    except (ValueError, TypeError) as e:
        return False, f"Authoritative receipt for '{ref_str}' has invalid ISO UTC timestamp format: {e}"

    # Verify canonical receipt hash recomputation
    recomputed_canon_hash = compute_canonical_receipt_hash(receipt_data)
    rec_canon_hash = receipt_data.get("canonical_receipt_hash", "")
    if not HEX_HASH_PATTERN.match(rec_canon_hash):
        return False, f"Authoritative receipt for '{ref_str}' canonical_receipt_hash '{rec_canon_hash}' is not a valid hex digest format."
    if rec_canon_hash != recomputed_canon_hash:
        return (
            False,
            f"Authoritative receipt for '{ref_str}' canonical_receipt_hash '{rec_canon_hash}' does not match recomputed hash '{recomputed_canon_hash}'.",
        )

    # Invoke concrete AuthoritativeReceiptVerifier
    ver_ok, ver_err = verifier_fn.verify(ref_str, receipt_data, entry)
    if not ver_ok:
        return False, f"Authoritative verifier rejected receipt for '{ref_str}': {ver_err or 'verification failed'}"

    return True, None


def is_valid_reason(reason: str) -> bool:
    """Validate that reason is a non-empty, non-trivial explanation of sufficient length."""
    if not reason or not isinstance(reason, str):
        return False
    r_str = reason.strip()
    if len(r_str) < 10:
        return False
    if r_str.lower() in TRIVIAL_REASON_VALUES:
        return False
    if len(set(r_str.lower())) < 4:
        return False
    words = [w for w in re.findall(r"[a-zA-Z0-9]+", r_str) if len(w) >= 3]
    if len(set(words)) < 2:
        return False
    return True


def validate_exemption_entry(
    entry: dict,
    exemption_type: str = "license",
    base_dir: Path | None = None,
    verifier_fn: Any | None = None,
) -> tuple[bool, list[str]]:
    """Validate a single exemption entry against the complete positive schema."""
    violations = []
    pkg_name = entry.get("package_name") or entry.get("purl") or "unknown"
    label = f"{exemption_type.capitalize()} exemption for '{pkg_name}'"

    if exemption_type == "license":
        req_keys = {"status", "approved_by", "approval_reference", "issued_at", "expires_at", "scope", "reason"}
        missing = req_keys - set(entry.keys())
        if "package_name" not in entry and "purl" not in entry:
            missing.add("package_name")
    else:  # vulnerability
        req_keys = {"package_name", "vulnerability_id", "status", "approved_by", "approval_reference", "issued_at", "expires_at", "scope", "reason"}
        missing = req_keys - set(entry.keys())

    if missing:
        violations.append(f"{label} is missing required fields: {sorted(missing)}")

    status = entry.get("status")
    if not status or status not in VALID_STATUSES:
        violations.append(f"{label} has invalid status '{status}'. Must be one of: {sorted(VALID_STATUSES)}")

    scope = entry.get("scope")
    if not scope or scope not in VALID_SCOPES:
        violations.append(f"{label} has invalid scope '{scope}'. Must be one of: {sorted(VALID_SCOPES)}")

    reason = entry.get("reason")
    if not is_valid_reason(reason):
        violations.append(f"{label} has invalid reason '{reason}'. Reason must be a non-trivial explanation (min 10 chars).")

    approver = entry.get("approved_by", "")
    if not is_valid_approver(approver):
        violations.append(
            f"{label} has invalid approver '{approver}'. "
            "Approver must be a named human/legal authority (e.g. 'Jane Doe (Legal Counsel)'); "
            "AI agent names, bare role tokens, and placeholder strings cannot serve as legal approvers."
        )

    app_ref = entry.get("approval_reference", "")
    if not is_valid_approval_reference(app_ref):
        violations.append(f"{label} is missing valid approval_reference.")

    issued_at = entry.get("issued_at")
    issued_dt = None
    if not issued_at or not isinstance(issued_at, str):
        violations.append(f"{label} is missing valid issued_at timestamp.")
    else:
        if not (issued_at.endswith("Z") or issued_at.endswith("+00:00") or issued_at.endswith("+0000")):
            violations.append(f"{label} issued_at timestamp '{issued_at}' must be ISO UTC (ending in 'Z' or '+00:00').")
        try:
            issued_dt = datetime.fromisoformat(issued_at.replace("Z", "+00:00"))
            if issued_dt > datetime.now(UTC) + timedelta(minutes=5):
                violations.append(f"{label} has future issued_at timestamp: '{issued_at}'.")
        except (ValueError, TypeError):
            violations.append(f"{label} has invalid issued_at timestamp: '{issued_at}'.")

    expires_at = entry.get("expires_at")
    expires_dt = None
    if not expires_at or not isinstance(expires_at, str):
        violations.append(f"{label} is missing valid expires_at timestamp.")
    else:
        if not (expires_at.endswith("Z") or expires_at.endswith("+00:00") or expires_at.endswith("+00:00")):
            violations.append(f"{label} expires_at timestamp '{expires_at}' must be ISO UTC (ending in 'Z' or '+00:00').")
        try:
            expires_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if expires_dt < datetime.now(UTC):
                violations.append(f"{label} expired at {expires_at}.")
        except (ValueError, TypeError):
            violations.append(f"{label} has invalid expires_at date: '{expires_at}'.")

    if issued_dt and expires_dt:
        if expires_dt <= issued_dt:
            violations.append(f"{label} has invalid timestamp ordering: expires_at ({expires_at}) must be after issued_at ({issued_at}).")

    if status == "active":
        res_ok, res_err = resolve_approval_reference(app_ref, entry, base_dir=base_dir, verifier_fn=verifier_fn)
        if not res_ok:
            violations.append(
                f"{label} has status 'active' but reference '{app_ref}' could not be resolved to an authentic legal policy receipt under ODP-PLAN-OSS-LEGAL-POLICY-001: {res_err}"
            )

    is_valid = len(violations) == 0
    return is_valid, violations


def filter_exemptions_by_scope(exemptions: list[dict], audit_scope: str) -> list[dict]:
    """Filter active exemptions by target audit scope (prod vs dev/full/all)."""
    if audit_scope in {"dev", "full", "all"}:
        return exemptions
    return [e for e in exemptions if e.get("scope", "all") in {"prod", "production", "all"}]

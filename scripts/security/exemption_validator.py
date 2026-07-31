"""Shared exemption schema and receipt validation for OSS license and vulnerability gates."""

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

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

    # Check for AI agent tokens or bare role tokens
    for pattern in [
        r"\bhuman/ops\b", r"\blegal/ops\b", r"\bsecurity/ops\b", r"\btbd/ops\b",
        r"\bclaude\b", r"\bgpt\b", r"\bgemini\b", r"\bantigravity\b", r"\bcodex\b", r"\bcopilot\b",
        r"^\s*(ops|legal|security|tbd|n/a|pending|unknown|none|null|zzzzz|fake|attacker|dummy)\s*$",
    ]:
        if re.search(pattern, app_lower):
            return False

    # Require named person (at least 2 name words) + role container/delimiter e.g. "Jane Doe (Legal Counsel)", "Jane Doe, Legal Counsel", "Alice Smith <Security Lead>"
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

    # Role part validation: must contain a recognized legal/security/compliance role
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
    # Enforce reference format e.g. ODP-PLAN-OSS-LEGAL-POLICY-001, SEC-1234, PR-123, ISSUE-456, LEGAL-2026-001, ADR-001
    ref_pattern = r"^(PR-?\d+|ISSUE-?\d+|SEC-\d+|LEGAL-\d+|ADR-\d+|POLICY-[A-Z0-9_-]+|[A-Z0-9]+-[A-Z0-9_-]+)$"
    if not re.match(ref_pattern, ref_str, re.IGNORECASE):
        return False
    return True


HEX_HASH_PATTERN = re.compile(r"^[a-fA-F0-9]{32,128}$")


def resolve_approval_reference(
    ref: str,
    entry: dict,
    base_dir: Path | None = None,
    verifier_fn: Callable[[str, dict, dict], tuple[bool, str | None] | bool] | None = None,
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

    # B1 rule: A repository-local lookalike file CANNOT self-establish authority without an authoritative external verifier / signed readback
    if verifier_fn is None:
        return (
            False,
            f"Repository-local receipt '{ref_str}' cannot self-establish authority without an authoritative external verifier or signed readback source under ODP-PLAN-OSS-LEGAL-POLICY-001.",
        )

    # Validate required authoritative receipt fields
    req_fields = {
        "principal_id",
        "principal_role",
        "source_system",
        "policy_decision",
        "policy_name",
        "policy_version",
        "policy_hash",
        "issued_at",
        "expires_at",
        "reviewed_at",
        "canonical_receipt_hash",
        "signature",
        "approved_by",
    }
    missing = [f for f in req_fields if not receipt_data.get(f) or not isinstance(receipt_data.get(f), str)]
    if missing:
        return False, f"Authoritative receipt for '{ref_str}' is missing required fields: {sorted(missing)}"

    # Verify principal role authorization
    rec_role = receipt_data.get("principal_role", "").lower().strip()
    if not any(r in rec_role for r in RECOGNIZED_ROLES):
        return False, f"Authoritative receipt for '{ref_str}' contains unauthorized principal_role '{receipt_data.get('principal_role')}'."

    # Verify status / policy decision
    p_decision = receipt_data.get("policy_decision")
    if p_decision not in {"approved", "active", "waived_with_conditions"}:
        return False, f"Authoritative receipt for '{ref_str}' policy_decision '{p_decision}' is not approved (must be 'approved', 'active', or 'waived_with_conditions')."

    rec_status = receipt_data.get("status", "approved")
    if rec_status not in {"approved", "active"}:
        return False, f"Authoritative receipt for '{ref_str}' status '{rec_status}' must be 'approved' or 'active'."

    # Verify field bindings: approved_by
    rec_approver = receipt_data.get("approved_by")
    entry_approver = entry.get("approved_by")
    if not rec_approver or rec_approver != entry_approver:
        return False, f"Authoritative receipt for '{ref_str}' approved_by '{rec_approver}' does not match entry approved_by '{entry_approver}'."

    if not is_valid_approver(rec_approver):
        return False, f"Authoritative receipt for '{ref_str}' contains invalid approver '{rec_approver}'."

    # Verify package_name / purl / vulnerability_id if present in receipt
    entry_pkg = entry.get("package_name") or entry.get("purl")
    rec_pkg = receipt_data.get("package_name") or receipt_data.get("purl")
    if rec_pkg and entry_pkg and rec_pkg != entry_pkg:
        return False, f"Authoritative receipt for '{ref_str}' package '{rec_pkg}' does not match entry package '{entry_pkg}'."

    entry_vid = entry.get("vulnerability_id")
    rec_vid = receipt_data.get("vulnerability_id")
    if rec_vid and entry_vid and rec_vid != entry_vid:
        return False, f"Authoritative receipt for '{ref_str}' vulnerability_id '{rec_vid}' does not match entry vulnerability_id '{entry_vid}'."

    # Verify scope if present in receipt
    entry_scope = entry.get("scope")
    rec_scope = receipt_data.get("scope")
    if rec_scope and entry_scope and rec_scope != entry_scope:
        return False, f"Authoritative receipt for '{ref_str}' scope '{rec_scope}' does not match entry scope '{entry_scope}'."

    # Verify timestamp formats, ordering, and expiration
    issued_at = receipt_data.get("issued_at", "")
    expires_at = receipt_data.get("expires_at", "")
    reviewed_at = receipt_data.get("reviewed_at", "")
    for ts_name, ts_val in [("issued_at", issued_at), ("expires_at", expires_at), ("reviewed_at", reviewed_at)]:
        if not (ts_val.endswith("Z") or ts_val.endswith("+00:00") or ts_val.endswith("+0000")):
            return False, f"Authoritative receipt for '{ref_str}' {ts_name} timestamp '{ts_val}' must be ISO UTC."

    try:
        issued_dt = datetime.fromisoformat(issued_at.replace("Z", "+00:00"))
        expires_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        if expires_dt <= issued_dt:
            return False, f"Authoritative receipt for '{ref_str}' expires_at ({expires_at}) must be after issued_at ({issued_at})."
        if expires_dt < datetime.now(UTC):
            return False, f"Authoritative receipt for '{ref_str}' expired at {expires_at}."
    except (ValueError, TypeError) as e:
        return False, f"Authoritative receipt for '{ref_str}' has invalid timestamp format: {e}"

    # Verify hash integrity formats
    pol_hash = receipt_data.get("policy_hash", "")
    rec_hash = receipt_data.get("canonical_receipt_hash", "")
    if not HEX_HASH_PATTERN.match(pol_hash):
        return False, f"Authoritative receipt for '{ref_str}' policy_hash '{pol_hash}' is not a valid hex digest format."
    if not HEX_HASH_PATTERN.match(rec_hash):
        return False, f"Authoritative receipt for '{ref_str}' canonical_receipt_hash '{rec_hash}' is not a valid hex digest format."

    # Invoke authoritative external verifier / readback callback
    try:
        ver_res = verifier_fn(ref_str, receipt_data, entry)
        if isinstance(ver_res, tuple):
            ver_ok, ver_err = ver_res
            if not ver_ok:
                return False, f"Authoritative verifier rejected receipt for '{ref_str}': {ver_err or 'verification failed'}"
        elif not ver_res:
            return False, f"Authoritative verifier rejected receipt for '{ref_str}': readback verification failed"
    except Exception as e:
        return False, f"Authoritative verifier raised error for '{ref_str}': {e}"

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
    # Reject single-character repetitions like "aaaaaaaaaa"
    if len(set(r_str.lower())) < 4:
        return False
    # Must contain at least 2 distinct words of length >= 3
    words = [w for w in re.findall(r"[a-zA-Z0-9]+", r_str) if len(w) >= 3]
    if len(set(words)) < 2:
        return False
    return True


def validate_exemption_entry(
    entry: dict,
    exemption_type: str = "license",
    base_dir: Path | None = None,
    verifier_fn: Callable[[str, dict, dict], tuple[bool, str | None] | bool] | None = None,
) -> tuple[bool, list[str]]:
    """Validate a single exemption entry against the complete positive schema.

    Returns:
        (is_valid, list_of_violations)
    """
    violations = []
    pkg_name = entry.get("package_name") or entry.get("purl") or "unknown"
    label = f"{exemption_type.capitalize()} exemption for '{pkg_name}'"

    # Required keys check
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

    # Status validation
    status = entry.get("status")
    if not status or status not in VALID_STATUSES:
        violations.append(f"{label} has invalid status '{status}'. Must be one of: {sorted(VALID_STATUSES)}")

    # Scope validation
    scope = entry.get("scope")
    if not scope or scope not in VALID_SCOPES:
        violations.append(f"{label} has invalid scope '{scope}'. Must be one of: {sorted(VALID_SCOPES)}")

    # Reason validation
    reason = entry.get("reason")
    if not is_valid_reason(reason):
        violations.append(f"{label} has invalid reason '{reason}'. Reason must be a non-trivial explanation (min 10 chars).")

    # Approver validation
    approver = entry.get("approved_by", "")
    if not is_valid_approver(approver):
        violations.append(
            f"{label} has invalid approver '{approver}'. "
            "Approver must be a named human/legal authority (e.g. 'Jane Doe (Legal Counsel)'); "
            "AI agent names, bare role tokens, and placeholder strings cannot serve as legal approvers."
        )

    # Approval reference validation
    app_ref = entry.get("approval_reference", "")
    if not is_valid_approval_reference(app_ref):
        violations.append(f"{label} is missing valid approval_reference.")

    # Issued at timestamp validation: must be ISO UTC ending with 'Z' or '+00:00'
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

    # Expires at timestamp & temporal ordering validation: must be ISO UTC ending with 'Z' or '+00:00'
    expires_at = entry.get("expires_at")
    expires_dt = None
    if not expires_at or not isinstance(expires_at, str):
        violations.append(f"{label} is missing valid expires_at timestamp.")
    else:
        if not (expires_at.endswith("Z") or expires_at.endswith("+00:00") or expires_at.endswith("+0000")):
            violations.append(f"{label} expires_at timestamp '{expires_at}' must be ISO UTC (ending in 'Z' or '+00:00').")
        try:
            expires_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if expires_dt < datetime.now(UTC):
                violations.append(f"{label} expired at {expires_at}.")
        except (ValueError, TypeError):
            violations.append(f"{label} has invalid expires_at date: '{expires_at}'.")

    # Temporal ordering: expires_at must be after issued_at
    if issued_dt and expires_dt:
        if expires_dt <= issued_dt:
            violations.append(f"{label} has invalid timestamp ordering: expires_at ({expires_at}) must be after issued_at ({issued_at}).")

    # Active exemption binding check: status 'active' requires resolvable authentic receipt owned by ODP-PLAN-OSS-LEGAL-POLICY-001
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

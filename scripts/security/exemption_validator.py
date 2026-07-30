"""Shared exemption schema and receipt validation for OSS license and vulnerability gates."""

import re
from datetime import UTC, datetime, timedelta

VALID_STATUSES = {"active", "inactive", "review_required"}
VALID_SCOPES = {"prod", "dev", "all", "production"}

# Disallowed approver patterns: AI names, bare roles, placeholders, short strings
REJECTED_APPROVER_EXACT = {
    "human/ops", "legal/ops", "security/ops", "tbd/ops", "unknown/ops",
    "n/a (ops)", "pending-legal", "ops", "legal", "security", "tbd", "n/a",
    "unknown", "none", "null", "undefined", "claude", "claudecode", "gpt",
    "gpt-4", "gemini", "antigravity", "codex", "copilot", "antigravity5",
    "codex2", "codex5", "codex6", "codex8", "codex9", "codexcoordinator",
    "antigravity2", "antigravity3", "antigravity4", "antigravity6", "antigravity7",
    "zzzzz",
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
        r"^\s*(ops|legal|security|tbd|n/a|pending|unknown|none|null|zzzzz)\s*$",
    ]:
        if re.search(pattern, app_lower):
            return False

    # Positively require named person (at least 2 name words) + role container/delimiter e.g. "Jane Doe (Legal Counsel)", "Jane Doe, Legal", "Alice Smith <Security Lead>"
    role_pattern = r"^([A-Za-z0-9\.\-']+(?:\s+[A-Za-z0-9\.\-']+)+)\s*[\(\<\,\-\[]\s*([A-Za-z0-9\s/_\-\.\,\&]{3,})[\)\>\-\]]?$"
    match = re.match(role_pattern, app_str)
    if not match:
        return False

    name_part, role_part = match.group(1).lower(), match.group(2).lower()
    if name_part in REJECTED_APPROVER_EXACT:
        return False
    for bad in ["claude", "gpt", "gemini", "antigravity", "codex", "copilot", "zzzzz", "tbd", "n/a", "unknown"]:
        if bad in name_part or bad in role_part:
            return False

    return True


def is_valid_approval_reference(ref: str) -> bool:
    """Validate that approval_reference is a non-empty, non-trivial resolvable reference contract."""
    if not ref or not isinstance(ref, str):
        return False
    ref_str = ref.strip()
    if not ref_str or len(ref_str) < 6:
        return False
    if ref_str.lower() in TRIVIAL_REF_VALUES:
        return False
    # Enforce resolvable reference format e.g. ODP-PLAN-OSS-LEGAL-POLICY-001, SEC-1234, PR-123, ISSUE-456, LEGAL-2026-001, ADR-001
    ref_pattern = r"^(PR-?\d+|ISSUE-?\d+|SEC-\d+|LEGAL-\d+|ADR-\d+|POLICY-[A-Z0-9_-]+|[A-Z0-9]+-[A-Z0-9_-]+)$"
    if not re.match(ref_pattern, ref_str, re.IGNORECASE):
        return False
    return True


def is_valid_reason(reason: str) -> bool:
    """Validate that reason is a non-empty, non-trivial explanation of sufficient length."""
    if not reason or not isinstance(reason, str):
        return False
    r_str = reason.strip()
    if len(r_str) < 10:
        return False
    if r_str.lower() in TRIVIAL_REASON_VALUES:
        return False
    return True


def validate_exemption_entry(entry: dict, exemption_type: str = "license") -> tuple[bool, list[str]]:
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

    is_valid = len(violations) == 0
    return is_valid, violations


def filter_exemptions_by_scope(exemptions: list[dict], audit_scope: str) -> list[dict]:
    """Filter active exemptions by target audit scope (prod vs dev/full/all)."""
    if audit_scope in {"dev", "full", "all"}:
        return exemptions
    return [e for e in exemptions if e.get("scope", "all") in {"prod", "production", "all"}]


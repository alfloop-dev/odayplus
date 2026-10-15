"""Render verified retained evidence into a reproducible JSON/CSV archive.

This renderer preserves existing evidence semantics and hash metadata. It does
not admit caller-supplied evidence, authorize access, or claim a PDF/signature.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from dataclasses import dataclass
from typing import Any

from shared.audit.integrity import canonical_json
from shared.audit.persistence import RetainedEvidence, verify_retained_evidence


class EvidenceArtifactUnavailable(ValueError):
    """The retained record cannot be rendered as an evidence artifact."""


@dataclass(frozen=True)
class EvidenceArtifact:
    content: bytes
    sha256: str
    media_type: str = "application/zip"


def validate_retained_evidence_artifact(record: RetainedEvidence) -> None:
    """Check retained artifact content without rendering bytes or admitting it."""
    verify_retained_evidence(record)
    if record.purged_at is not None:
        raise EvidenceArtifactUnavailable("retained evidence has been purged")
    bundle = record.bundle
    governance = bundle.get("export_governance")
    if not isinstance(governance, dict) or governance.get("masking_profile") != "masked":
        raise EvidenceArtifactUnavailable("masked evidence bundle is required")
    for key in ("decision_cards", "audit_events", "subsidy_matrix"):
        rows = bundle.get(key)
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise EvidenceArtifactUnavailable(f"invalid retained {key}")


def render_evidence_archive(record: RetainedEvidence) -> EvidenceArtifact:
    """Use only the persisted, integrity-verified bundle; never rebuild evidence."""
    validate_retained_evidence_artifact(record)
    bundle = record.bundle
    files = {
        "bundle.json": _json_bytes(bundle),
        "retained-evidence.json": _json_bytes(record.to_dict()),
    }
    for key, filename in (
        ("decision_cards", "decision-cards.csv"),
        ("audit_events", "audit-events.csv"),
        ("subsidy_matrix", "subsidy-matrix.csv"),
    ):
        rows = bundle.get(key)
        files[filename] = _csv_bytes(rows)
    files["manifest.json"] = _json_bytes(
        {
            "format": "oday-audit-evidence-json-csv-zip-v1",
            "export_id": record.export_id,
            "bundle_checksum": record.bundle_checksum,
            "retained_record_integrity": record.summary()["integrity"],
            "generated_at": record.generated_at.isoformat(),
            "csv_formula_escaping": "leading apostrophe; original values in bundle.json",
            "files": [
                {
                    "path": filename,
                    "bytes": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
                for filename, content in sorted(files.items())
            ],
        }
    )
    output = io.BytesIO()
    # A fixed ZIP timestamp and stored entries make repeat downloads byte-identical
    # across restarts without depending on the compression library version.
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        for filename, content in sorted(files.items()):
            entry = zipfile.ZipInfo(filename, date_time=(1980, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = 0o100600 << 16
            archive.writestr(entry, content)
    content = output.getvalue()
    return EvidenceArtifact(content=content, sha256=hashlib.sha256(content).hexdigest())


def _json_bytes(value: Any) -> bytes:
    return (canonical_json(value) + "\n").encode("utf-8")


def _csv_bytes(rows: list[dict[str, Any]]) -> bytes:
    output = io.StringIO(newline="")
    fields = sorted({field for row in rows for field in row})
    if fields:
        writer = csv.writer(output, lineterminator="\r\n")
        writer.writerow([_csv_cell(field) for field in fields])
        for row in rows:
            writer.writerow([_csv_cell(row.get(field)) for field in fields])
    return output.getvalue().encode("utf-8")


def _csv_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple, bool)):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    else:
        text = str(value)
    # Excel/LibreOffice may treat leading whitespace/control characters followed
    # by a formula marker as executable. JSON always retains the original value.
    if text.startswith(("\t", "\r", "\n")) or text.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + text
    return text

"""Actual JSON, CSV ZIP and embedded-font PDF for one safe general projection.

Formats share one exact allowlist. This module never grants read/export access,
recreates a score, serializes SQL retained metadata or supplies missing outcome.
The current-authority reader must validate the record before calling it.
"""
from __future__ import annotations

import hashlib
import io
import json
import zipfile

from modules.opsboard.audit.application.evidence_artifact import (
    EvidenceArtifact,
    EvidenceArtifactUnavailable,
    _csv_bytes,
)
from modules.opsboard.audit.application.evidence_general_document import validate_general_document
from modules.opsboard.audit.application.evidence_pdf import _FONT_NAME, _font
from modules.opsboard.audit.application.sitescore_evidence import SiteScoreEvidenceUnavailable
from shared.audit.integrity import canonical_json


def _artifact(content: bytes, media_type: str) -> EvidenceArtifact:
    return EvidenceArtifact(content, hashlib.sha256(content).hexdigest(), media_type)


def render_general_document(document: dict, format: str) -> EvidenceArtifact:
    document = validate_general_document(document)
    if format == "json":
        return _artifact(canonical_json(document).encode("utf-8"), "application/json")
    if format == "csv":
        files = {"canonical-evidence.json": canonical_json(document).encode("utf-8")}
        for name, rows in document["sections"].items():
            files[{"decision_log": "decision-log.csv", "audit": "audit.csv"}[name]] = _csv_bytes(rows)
        if "recorded_chain" in document:
            chain = document["recorded_chain"]
            files["recorded-decision-chain.json"] = canonical_json(chain).encode("utf-8")
            flattened = []
            def leaves(value, path):
                if isinstance(value, dict):
                    for name, item in value.items():
                        leaves(item, path + "." + name)
                elif isinstance(value, list):
                    for index, item in enumerate(value):
                        leaves(item, path + "[" + str(index) + "]")
                    if not value:
                        flattened.append({"field": path, "value": "[]"})
                else:
                    flattened.append({"field": path, "value": canonical_json(value)})
            leaves(chain, "recorded_chain")
            files["recorded-decision-chain.csv"] = _csv_bytes(flattened)
        manifest = {"schema": "sitescore.general-csv-manifest.v1", "source_digest": document["source_digest"],
            "classification": document["classification"], "masking_profile": "masked", "limits": document["limits"],
            "rows": {name: len(rows) for name, rows in document["sections"].items()},
            "files": [{"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                for name, data in sorted(files.items())]}
        files["manifest.json"] = canonical_json(manifest).encode("utf-8")
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in sorted(files.items()):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100440 << 16
                archive.writestr(info, data)
        return _artifact(output.getvalue(), "application/zip")
    if format == "pdf":
        text = "Oday Plus recorded SiteScore Decision/Audit evidence\n"
        text += ("Recorded partial chain; unavailable outcome/execution is not supplied.\n\n" if "recorded_chain" in document
            else "Reference coverage only; missing execution/outcome is not supplied.\n\n")
        text += json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        return _pdf(text)
    raise SiteScoreEvidenceUnavailable("CANONICAL_EVIDENCE_INPUT_REQUIRED:unimplemented_artifact_format")


def _pdf(content: str) -> EvidenceArtifact:
    # The actual original PDF renderer's font validation/geometry/pagination
    # is reused below; plain text is never interpreted as HTML or a URL.
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfgen.canvas import Canvas

    font = _font()
    missing = sorted(
        {ord(char) for char in content if char != "\n" and ord(char) not in font.face.charToGlyph}
    )
    if missing:
        raise EvidenceArtifactUnavailable(f"unsupported PDF glyph U+{missing[0]:04X}")

    output = io.BytesIO()
    canvas = Canvas(output, pagesize=A4, invariant=1, pageCompression=1)
    canvas.setTitle("Oday Plus Audit Evidence")
    canvas.setAuthor("Oday Plus")
    canvas.setCreator("Oday Plus canonical general reference PDF renderer v1")
    width, height = A4
    margin, font_size, line_height = 40, 8, 12
    usable_width = width - 2 * margin
    page = 0
    y = 0.0

    def start_page() -> None:
        nonlocal page, y
        page += 1
        canvas.setFont(_FONT_NAME, 12)
        canvas.drawString(margin, height - margin, "Oday Plus Audit Evidence")
        canvas.setFont(_FONT_NAME, 7)
        canvas.drawRightString(width - margin, 22, f"Page {page}")
        canvas.setFont(_FONT_NAME, font_size)
        y = height - margin - 28

    start_page()
    widths: dict[str, float] = {}
    for source_line in content.split("\n"):
        # Character wrapping also handles long IDs/URLs and CJK without spaces.
        lines: list[str] = []
        current = ""
        current_width = 0.0
        for char in source_line:
            if char not in widths:
                widths[char] = pdfmetrics.stringWidth(char, _FONT_NAME, font_size)
            char_width = widths[char]
            if current and current_width + char_width > usable_width:
                lines.append(current)
                current, current_width = "", 0.0
            current += char
            current_width += char_width
        lines.append(current)
        for line in lines:
            if y < margin:
                canvas.showPage()
                start_page()
            canvas.drawString(margin, y, line)
            y -= line_height
    canvas.save()
    data = output.getvalue()
    return EvidenceArtifact(
        content=data, sha256=hashlib.sha256(data).hexdigest(), media_type="application/pdf"
    )

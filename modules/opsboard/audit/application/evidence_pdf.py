"""Portable Unicode PDF from an existing verified retained evidence record."""

from __future__ import annotations

import hashlib
import io
import json
from functools import lru_cache
from pathlib import Path

from modules.opsboard.audit.application.evidence_artifact import (
    EvidenceArtifact,
    EvidenceArtifactUnavailable,
)
from shared.audit.persistence import RetainedEvidence, verify_retained_evidence

_ASSETS = Path(__file__).resolve().parents[1] / "assets" / "ibm-plex-sans-tc"
_FONT_NAME = "ODPEvidencePlexTC"


@lru_cache(maxsize=1)
def _font():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    source = json.loads((_ASSETS / "SOURCE.json").read_text())
    expected = next(item for item in source["files"] if item["file"].endswith(".ttf"))
    path = _ASSETS / expected["file"]
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected["sha256"]:
        raise EvidenceArtifactUnavailable("PDF font integrity mismatch")
    font = TTFont(_FONT_NAME, str(path))
    pdfmetrics.registerFont(font)
    return font


def render_evidence_pdf(record: RetainedEvidence) -> EvidenceArtifact:
    """Embed the font, paginate every field, and refuse unsupported glyphs.

    JSON is drawn as plain text, never parsed as HTML or evaluated as a link.
    The same record produces the same bytes; PDF creation metadata does not
    introduce a new wall-clock timestamp or an invented approval/signature.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfgen.canvas import Canvas

    verify_retained_evidence(record)
    if record.purged_at is not None:
        raise EvidenceArtifactUnavailable("retained evidence has been purged")
    governance = record.bundle.get("export_governance")
    if not isinstance(governance, dict) or governance.get("masking_profile") != "masked":
        raise EvidenceArtifactUnavailable("masked evidence bundle is required")

    # Include the complete retained record rather than selecting only ready rows
    # or omitting a long value. The original structured ZIP remains available.
    content = "Export summary\n" + "\n".join(
        f"{key}: {record.bundle[key]}"
        for key in (
            "export_id",
            "program_id",
            "purpose",
            "requested_by",
            "generated_at",
            "period_start",
            "period_end",
            "bundle_checksum",
            "missing_requirements",
        )
    )
    for key, title in (
        ("decision_cards", "Decision cards"),
        ("audit_events", "Audit events"),
        ("subsidy_matrix", "Subsidy evidence matrix"),
    ):
        content += f"\n\n{title}\n"
        for index, row in enumerate(record.bundle[key], start=1):
            content += (
                f"\n{title} #{index}\n"
                + json.dumps(row, ensure_ascii=False, sort_keys=True, indent=2)
                + "\n"
            )
    content += "\n\nComplete retained record (all fields)\n" + json.dumps(
        record.to_dict(), ensure_ascii=False, sort_keys=True, indent=2
    )
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
    canvas.setCreator("Oday Plus retained evidence PDF renderer v1")
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

from __future__ import annotations

import hashlib
import io
from dataclasses import replace

import pytest
from pypdf import PdfReader

from modules.opsboard.audit.application.evidence_artifact import EvidenceArtifactUnavailable
from modules.opsboard.audit.application.evidence_pdf import render_evidence_pdf
from shared.audit.persistence import EvidenceIntegrityError, attach_retained_evidence_integrity
from tests.integration.test_audit_evidence_artifact import _client, _headers, _retained


def test_pdf_embeds_unicode_font_preserves_fields_and_reproduces_bytes():
    record = _retained(title="中文核准證據")
    artifact = render_evidence_pdf(record)
    assert artifact.media_type == "application/pdf"
    assert artifact.content.startswith(b"%PDF-")
    assert artifact == render_evidence_pdf(record)
    assert hashlib.sha256(artifact.content).hexdigest() == artifact.sha256
    reader = PdfReader(io.BytesIO(artifact.content))
    text = "".join(page.extract_text() for page in reader.pages)
    for value in (
        "中文核准證據",
        "中文理由",
        record.export_id,
        record.bundle_checksum,
        record.record_hash,
        "Complete retained record",
        "subsidy_matrix",
        "audit_events",
        "decision_cards",
    ):
        assert value in text
    assert "\ufffd" not in text
    assert any(
        font.get_object().get("/FontDescriptor", {}).get("/FontFile2")
        for page in reader.pages
        for font in page["/Resources"]["/Font"].values()
        if font.get_object().get("/FontDescriptor")
    )
    assert not reader.trailer["/Root"].get("/OpenAction")
    assert not reader.trailer["/Root"].get("/Names")


def test_pdf_paginates_long_values_without_truncating_or_creating_links():
    title = "javascript:alert(1) <script>" + "中文測試內容" * 700 + "END-OF-LONG-VALUE"
    artifact = render_evidence_pdf(_retained(title=title))
    reader = PdfReader(io.BytesIO(artifact.content))
    assert len(reader.pages) > 6
    text = "".join(page.extract_text() for page in reader.pages)
    assert "END-OF-LONG-VALUE" in text.replace("\n", "") and "javascript:alert(1)" in text
    assert not any(page.get("/Annots") for page in reader.pages)
    # Check drawing positions independently of the renderer's wrapping logic.
    for page in reader.pages:
        positions = []
        page.extract_text(
            visitor_text=lambda text, _cm, tm, _font, _size, positions=positions: (
                positions.append((text, tm[4], tm[5])) if text.strip() else None
            )
        )
        assert all(
            20 <= x <= float(page.mediabox.width) - 20
            and 15 <= y <= float(page.mediabox.height) - 15
            for _text, x, y in positions
        )


def test_pdf_rejects_unsupported_glyph_and_tampered_record():
    with pytest.raises(EvidenceArtifactUnavailable, match="unsupported PDF glyph U\\+10FFFF"):
        render_evidence_pdf(_retained(title="\U0010ffff"))
    record = _retained()
    with pytest.raises(EvidenceIntegrityError):
        render_evidence_pdf(replace(record, bundle={**record.bundle, "purpose": "tampered"}))
    unmasked = replace(
        record,
        bundle={
            **record.bundle,
            "export_governance": {
                **record.bundle["export_governance"],
                "masking_profile": "unmasked",
            },
        },
    )
    unmasked = attach_retained_evidence_integrity(
        unmasked, sequence=record.sequence, previous_hash=record.previous_hash
    )
    with pytest.raises(EvidenceArtifactUnavailable, match="masked"):
        render_evidence_pdf(unmasked)


def test_pdf_download_uses_same_ownership_and_real_hash_audit():
    client, store, body, audit_log = _client()
    body["decision_cards"][0]["title"] = "中文下載證據"
    response = client.post("/audit/evidence/export", json=body)
    assert response.status_code == 201
    export_id = response.json()["export_id"]
    path = f"/audit/evidence/exports/{export_id}/download?format=pdf"
    pdf = client.get(path)
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.headers["content-disposition"].endswith('"audit-evidence.pdf"')
    assert pdf.content == render_evidence_pdf(store.get(export_id)).content
    assert pdf.headers["x-artifact-sha256"] == hashlib.sha256(pdf.content).hexdigest()
    assert "中文下載證據" in "".join(
        page.extract_text() for page in PdfReader(io.BytesIO(pdf.content)).pages
    )
    assert client.get(path, headers=_headers(subject="other")).status_code == 404
    assert client.get(path, headers=_headers(tenant="other")).status_code == 404
    assert client.get(path.replace("format=pdf", "format=html")).status_code == 422
    events = [
        event
        for event in audit_log.list_events()
        if event.event_type == "audit.evidence_download.v1"
    ]
    assert len(events) == 1 and events[0].metadata["format"] == "PDF"
    assert events[0].metadata["artifact_sha256"] == pdf.headers["x-artifact-sha256"]
    contract = client.get("/openapi.json").json()["paths"][
        "/audit/evidence/exports/{export_id}/download"
    ]["get"]
    assert contract["responses"]["200"]["content"]["application/pdf"]["schema"] == {
        "type": "string",
        "format": "binary",
    }

"""Actual binary format controls over canonical engineering projection only."""
import csv
import hashlib
import io
import json
import zipfile
from copy import deepcopy

import pytest
from pypdf import PdfReader

from modules.opsboard.audit.application.evidence_artifact import EvidenceArtifactUnavailable
from modules.opsboard.audit.application.evidence_general_formats import render_general_document
from modules.opsboard.audit.application.sitescore_evidence import SiteScoreEvidenceUnavailable
from tests.integration.test_audit_sitescore_evidence_resolver import case as case
from tests.integration.test_audit_sitescore_general_admission import setup


def document(case):
    owner, actor, selection = setup(case)
    return owner.resolve(actor, selection).document()


def test_actual_JSON_CSV_zip_and_embedded_PDF_share_identical_safe_fields_and_reproducible_hashes(case):
    value = document(case)
    artifacts = {format: render_general_document(value, format) for format in ("json", "csv", "pdf")}
    for format, artifact in artifacts.items():
        assert artifact == render_general_document(value, format)
        assert hashlib.sha256(artifact.content).hexdigest() == artifact.sha256
    assert artifacts["json"].media_type == "application/json"
    assert json.loads(artifacts["json"].content) == value
    assert artifacts["csv"].media_type == "application/zip" and zipfile.is_zipfile(io.BytesIO(artifacts["csv"].content))
    with zipfile.ZipFile(io.BytesIO(artifacts["csv"].content)) as archive:
        assert archive.testzip() is None
        assert set(archive.namelist()) == {"canonical-evidence.json", "decision-log.csv", "audit.csv", "manifest.json"}
        assert json.loads(archive.read("canonical-evidence.json")) == value
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["source_digest"] == value["source_digest"] and manifest["limits"] == value["limits"]
        for item in manifest["files"]:
            data = archive.read(item["path"])
            assert item["bytes"] == len(data) and item["sha256"] == hashlib.sha256(data).hexdigest()
        for section, name in (("decision_log", "decision-log.csv"), ("audit", "audit.csv")):
            rows = list(csv.DictReader(io.StringIO(archive.read(name).decode())))
            assert len(rows) == len(value["sections"][section])
            assert set(rows[0]) == set(value["sections"][section][0])
    pdf = artifacts["pdf"]
    assert pdf.media_type == "application/pdf" and pdf.content.startswith(b"%PDF-")
    reader = PdfReader(io.BytesIO(pdf.content))
    text = "".join(page.extract_text() for page in reader.pages)
    assert value["source_digest"] in text.replace("\n", "")
    assert "recorded_decision_and_audit_references_only" in text.replace("\n", "")
    assert not reader.trailer["/Root"].get("/OpenAction") and not any(page.get("/Annots") for page in reader.pages)
    assert any(font.get_object().get("/FontDescriptor", {}).get("/FontFile2")
        for page in reader.pages for font in page["/Resources"]["/Font"].values()
        if font.get_object().get("/FontDescriptor"))


@pytest.mark.parametrize("format", ["json", "csv", "pdf"])
@pytest.mark.parametrize("tamper", ["opaque", "nested", "class", "mask_flag", "unmasked_actor", "wrong_type"])
def test_every_format_refuses_identical_invalid_or_unmasked_projection(case, format, tamper):
    value = deepcopy(document(case))
    if tamper == "opaque":
        value["raw_report"] = {"private": "not admitted"}
    elif tamper == "nested":
        value["sections"]["decision_log"][0]["approval_record"] = {"caller_claim": "not authoritative"}
    elif tamper == "class":
        value["classification"] = "RESTRICTED"
    elif tamper == "mask_flag":
        value["actor_fields_masked"] = False
    elif tamper == "unmasked_actor":
        value["sections"]["decision_log"][0]["decision_actor"] = "unmasked-private-actor"
    else:
        value["sections"]["decision_log"][0]["recorded_model_reference"] = {"invalid": "type"}
    with pytest.raises(SiteScoreEvidenceUnavailable):
        render_general_document(value, format)


@pytest.mark.parametrize("value", ["=CMD(1)", "+command", "-command", "@command", " \r=command"])
def test_CSV_formula_escape_does_not_change_canonical_JSON_or_projection_fields(case, value):
    doc = deepcopy(document(case))
    # Deliberately adversarial renderer input, not a claim that a new model
    # reference was produced/admitted by a canonical scoring owner.
    doc["sections"]["decision_log"][0]["recorded_model_reference"] = value
    artifact = render_general_document(doc, "csv")
    with zipfile.ZipFile(io.BytesIO(artifact.content)) as archive:
        row = next(csv.DictReader(io.StringIO(archive.read("decision-log.csv").decode())))
        assert row["recorded_model_reference"] == "'" + value
        assert json.loads(archive.read("canonical-evidence.json"))["sections"]["decision_log"][0]["recorded_model_reference"] == value


def test_PDF_true_unicode_pagination_keeps_all_values_without_links_or_truncation(case):
    value = deepcopy(document(case))
    model = "中文測試" * 2500 + "END-OF-RECORDED-REFERENCE"
    value["sections"]["decision_log"][0]["recorded_model_reference"] = model
    artifact = render_general_document(value, "pdf")
    reader = PdfReader(io.BytesIO(artifact.content))
    assert len(reader.pages) > 3
    text = "".join(page.extract_text() for page in reader.pages).replace("\n", "")
    assert "中文測試" in text and "END-OF-RECORDED-REFERENCE" in text and "\ufffd" not in text
    assert not any(page.get("/Annots") for page in reader.pages)
    for page in reader.pages:
        positions = []
        page.extract_text(visitor_text=lambda text, _cm, tm, _font, _size, positions=positions:
            positions.append((tm[4], tm[5])) if text.strip() else None)
        assert all(20 <= x <= float(page.mediabox.width) - 20 and 15 <= y <= float(page.mediabox.height) - 15
            for x, y in positions)


def test_PDF_unsupported_glyph_and_unimplemented_format_refuse_honestly(case):
    value = deepcopy(document(case))
    value["sections"]["decision_log"][0]["recorded_model_reference"] = "\U0010ffff"
    with pytest.raises(EvidenceArtifactUnavailable, match="unsupported PDF glyph"):
        render_general_document(value, "pdf")
    with pytest.raises(SiteScoreEvidenceUnavailable, match="unimplemented_artifact_format"):
        render_general_document(document(case), "html")

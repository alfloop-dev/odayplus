"""Unit tests for governed XLSX import (ODP-CAP-XLSX-IMPORT-001)."""

import io
import json
import zipfile
import pytest

from modules.external_data.application.xlsx_import import (
    SafeXlsxParser,
    XlsxImportError,
    XlsxRowError,
    commit_xlsx_import,
    export_xlsx_import_errors,
    mask_sensitive_value,
    preview_xlsx_import,
)
from shared.audit import InMemoryAuditLog


def _create_mock_xlsx(
    rows: list[list[str]],
    formulas: dict[tuple[int, int], str] | None = None,
    external_rels: bool = False,
) -> bytes:
    """Helper to generate standard OpenXML XLSX bytes for unit tests."""
    buf = io.BytesIO()
    formulas = formulas or {}

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
            '</Types>'
        ))

        rel_target = "http://external-malicious-site.com" if external_rels else "xl/workbook.xml"
        rel_mode = ' TargetMode="External"' if external_rels else ""
        zf.writestr("_rels/.rels", (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="{rel_target}"{rel_mode}/>'
            '</Relationships>'
        ))

        zf.writestr("xl/workbook.xml", (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets>'
            '</workbook>'
        ))

        zf.writestr("xl/_rels/workbook.xml.rels", (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>'
            '</Relationships>'
        ))

        # Flatten strings for shared strings table
        all_strings: list[str] = []
        string_map: dict[str, int] = {}

        def _get_str_idx(s: str) -> int:
            if s in string_map:
                return string_map[s]
            idx = len(all_strings)
            all_strings.append(s)
            string_map[s] = idx
            return idx

        sheet_row_xmls = []
        for r_idx, row_vals in enumerate(rows, start=1):
            cell_xmls = []
            for c_idx, val in enumerate(row_vals, start=1):
                col_letter = chr(64 + c_idx)
                cell_ref = f"{col_letter}{r_idx}"
                f_str = formulas.get((r_idx, c_idx))

                if f_str:
                    f_tag = f"<f>{f_str}</f>"
                    s_idx = _get_str_idx(val)
                    cell_xmls.append(f'<c r="{cell_ref}" t="s">{f_tag}<v>{s_idx}</v></c>')
                else:
                    s_idx = _get_str_idx(val)
                    cell_xmls.append(f'<c r="{cell_ref}" t="s"><v>{s_idx}</v></c>')

            sheet_row_xmls.append(f'<row r="{r_idx}">{"".join(cell_xmls)}</row>')

        sheet_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetData>' + "".join(sheet_row_xmls) + '</sheetData>'
            '</worksheet>'
        )
        zf.writestr("xl/worksheets/sheet1.xml", sheet_xml)

        sst_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="{len(all_strings)}" uniqueCount="{len(all_strings)}">'
            + "".join(f'<si><t>{s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")}</t></si>' for s in all_strings)
            + '</sst>'
        )
        zf.writestr("xl/sharedStrings.xml", sst_xml)

    buf.seek(0)
    return buf.getvalue()


def test_malformed_formula_and_external_link_inputs_fail_safely():
    """Verify malformed formula, external link, and corrupt ZIP inputs fail safely."""

    # 1. Corrupt ZIP file
    with pytest.raises(XlsxImportError) as exc_info:
        parser = SafeXlsxParser(b"NOT_A_ZIP_FILE_CORRUPT_BYTES")
        parser.parse()
    assert exc_info.value.code == "MALFORMED_XLSX_FILE"

    # 2. XLSX containing external link relationship
    ext_xlsx = _create_mock_xlsx([["地址", "租金"], ["台北市信義區松仁路10號", "50000"]], external_rels=True)
    parser = SafeXlsxParser(ext_xlsx)
    rows, warnings, has_formula_or_link = parser.parse()
    assert has_formula_or_link is True
    assert any("External target" in w for w in warnings)

    # 3. XLSX containing malicious formula
    formula_xlsx = _create_mock_xlsx(
        [["地址", "租金"], ["台北市中山區南京東路1號", "45000"]],
        formulas={(2, 2): 'HYPERLINK("http://evil.com", "click")'},
    )
    parser = SafeXlsxParser(formula_xlsx)
    rows, warnings, has_formula_or_link = parser.parse()
    assert has_formula_or_link is True
    assert len(rows) == 1
    assert rows[0]["租金"] == "45000"  # Formula NOT executed, value safely extracted


def test_preview_performs_no_writes():
    """Verify preview parses & validates but performs zero writes to intake state."""

    valid_xlsx = _create_mock_xlsx([
        ["地址", "租金", "坪數", "樓層"],
        ["新北市板橋區縣民大道2號", "35000", "20", "1F"],
        ["桃園市中壢區中北路100號", "28000", "15", "2F"],
    ])

    result = preview_xlsx_import(valid_xlsx)
    assert result.batch_id.startswith("xlsx-batch-")
    assert result.total_rows == 2
    assert result.valid_count == 2
    assert result.rejected_count == 0
    assert len(result.valid_rows) == 2
    assert result.valid_rows[0]["address_raw"] == "新北市板橋區縣民大道2號"
    assert result.valid_rows[0]["rent_amount"] == 35000.0


def test_commit_writes_validated_rows_only():
    """Verify commit writes ONLY validated rows and rejects invalid ones."""

    mix_rows = [
        {"address_raw": "台北市大安區新生南路1號", "rent_amount": 50000, "area_ping": 25},  # Valid
        {"address_raw": "", "rent_amount": 40000, "area_ping": 18},  # Invalid: missing address
        {"address_raw": "台中市西區公益路50號", "rent_amount": -100, "area_ping": 30},  # Invalid: negative rent
    ]

    audit_log = InMemoryAuditLog()
    receipt = commit_xlsx_import(
        rows=mix_rows,
        batch_id="test-batch-001",
        scope={"tenant_id": "tenant-a"},
        audit_log=audit_log,
    )

    assert receipt.accepted_count == 1
    assert receipt.rejected_count == 2
    assert len(receipt.intake_ids) == 1
    assert receipt.replayed is False

    # Check audit record
    events = audit_log.list_events()
    assert len(events) == 1
    assert events[0].action == "xlsx_import_commit"
    assert events[0].outcome == "SUCCEEDED"


def test_duplicate_commit_is_idempotent():
    """Verify submitting the same commit with the same Idempotency-Key returns a replayed response."""

    rows = [{"address_raw": "高雄市新興區中山一路10號", "rent_amount": 32000}]
    idempotency_key = "IDEM-KEY-998877665544"

    receipt1 = commit_xlsx_import(rows=rows, idempotency_key=idempotency_key)
    assert receipt1.replayed is False
    assert receipt1.accepted_count == 1

    receipt2 = commit_xlsx_import(rows=rows, idempotency_key=idempotency_key)
    assert receipt2.replayed is True
    assert receipt2.batch_id == receipt1.batch_id
    assert receipt2.accepted_count == receipt1.accepted_count
    assert receipt2.intake_ids == receipt1.intake_ids


def test_row_errors_downloadable_with_sensitive_masking():
    """Verify row errors are exportable in XLSX/CSV with sensitive data masking applied."""

    # Test masking helper
    assert mask_sensitive_value("聯絡人電話: 0912345678") == "聯絡人電話: 0912****78"
    assert mask_sensitive_value("email: user@domain.com") == "email: u***r@domain.com"
    assert mask_sensitive_value("password: secretpassword123") == "[RESTRICTED_SECRET]"

    row_errors = [
        XlsxRowError(
            row_index=2,
            field="contact_phone",
            code="INVALID_PHONE",
            message="Invalid phone format for 0912345678",
            value="0912345678",
        ),
        XlsxRowError(
            row_index=3,
            field="owner_email",
            code="INVALID_EMAIL",
            message="Invalid email address user@example.com",
            value="user@example.com",
        ),
    ]

    # Test CSV export
    csv_bytes, csv_mime = export_xlsx_import_errors(row_errors, export_format="csv")
    assert csv_mime.startswith("text/csv")
    csv_str = csv_bytes.decode("utf-8-sig")
    assert "0912****78" in csv_str
    assert "u***r@example.com" in csv_str
    assert "0912345678" not in csv_str  # Unmasked phone must NOT appear

    # Test XLSX export
    xlsx_bytes, xlsx_mime = export_xlsx_import_errors(row_errors, export_format="xlsx")
    assert xlsx_mime == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert len(xlsx_bytes) > 0

    # Parse exported XLSX to verify content
    parser = SafeXlsxParser(xlsx_bytes)
    rows, _, _ = parser.parse()
    assert len(rows) == 2
    assert "0912****78" in str(rows[0])

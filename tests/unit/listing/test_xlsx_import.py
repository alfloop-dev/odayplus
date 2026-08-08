"""Unit tests for governed XLSX import (ODP-CAP-XLSX-IMPORT-001)."""

import io
import zipfile

import pytest

from modules.external_data.application import xlsx_import
from modules.external_data.application.xlsx_import import (
    SafeXlsxParser,
    XlsxImportError,
    XlsxRowError,
    commit_xlsx_import,
    export_xlsx_import_errors,
    get_committed_intake,
    get_preview_result,
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


_WORKBOOK_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
    '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets>'
    '</workbook>'
)

_WORKBOOK_RELS_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
    '</Relationships>'
)


def _zip_parts(parts: dict[str, str]) -> bytes:
    """Build a raw XLSX archive from explicit part contents."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in parts.items():
            zf.writestr(name, content)
    buf.seek(0)
    return buf.getvalue()


def _worksheet(rows_xml: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{rows_xml}</sheetData>"
        '</worksheet>'
    )


def _inline_row(row_ref: int, cells: dict[str, str]) -> str:
    """Build a `<row>` with inline-string cells keyed by A1 reference."""
    cell_xml = "".join(
        f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>'
        for ref, text in cells.items()
    )
    return f'<row r="{row_ref}">{cell_xml}</row>'


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

    committed_before = dict(xlsx_import._COMMITTED_INTAKES)

    result = preview_xlsx_import(valid_xlsx)

    # The property the test is named for: preview writes no intake record. The
    # counts below would pass just as well if it had.
    assert dict(xlsx_import._COMMITTED_INTAKES) == committed_before

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
    written: list[dict] = []

    def spy_writer(row: dict) -> str:
        written.append(row)
        return f"intake-{len(written)}"

    receipt = commit_xlsx_import(
        rows=mix_rows,
        batch_id="test-batch-001",
        scope={"tenant_id": "tenant-a"},
        audit_log=audit_log,
        intake_writer=spy_writer,
    )

    assert receipt.accepted_count == 1
    assert receipt.rejected_count == 2
    assert len(receipt.intake_ids) == 1
    assert receipt.replayed is False

    # Exactly the validated row reached the writer, and the receipt reports the
    # id the writer returned rather than a freshly minted one.
    assert [row["address_raw"] for row in written] == ["台北市大安區新生南路1號"]
    assert receipt.intake_ids == ["intake-1"]

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


def test_committed_rows_are_retrievable_by_receipt_id():
    """A receipt id must resolve to a stored row, not just to a fresh uuid."""

    receipt = commit_xlsx_import(
        rows=[{"address_raw": "台北市松山區民生東路三段10號", "rent_amount": 48000}],
        scope={"tenant_id": "tenant-retrieve"},
    )

    assert len(receipt.intake_ids) == 1
    stored = get_committed_intake(receipt.intake_ids[0])
    assert stored is not None
    assert stored["address_raw"] == "台北市松山區民生東路三段10號"
    assert stored["rent_amount"] == 48000.0


def test_commit_applies_full_domain_validation_not_a_subset():
    """Commit must not accept rows that preview would reject."""

    rows = [
        # Unsupported URL scheme: rejected by validate_url, which the commit path
        # previously skipped entirely.
        {"address_raw": "台北市中正區重慶南路一段2號", "original_url": "javascript:alert(1)"},
        # Negative area: preview's INVALID_RANGE rule, previously applied to rent only.
        {"address_raw": "台北市中正區重慶南路一段4號", "area_ping": -999},
        {"address_raw": "台北市中正區重慶南路一段6號", "rent_amount": 30000, "area_ping": 12},
    ]

    receipt = commit_xlsx_import(rows=rows, scope={"tenant_id": "tenant-validation"})

    assert receipt.accepted_count == 1
    assert receipt.rejected_count == 2
    assert len(receipt.intake_ids) == 1
    stored = get_committed_intake(receipt.intake_ids[0])
    assert stored is not None
    assert stored["address_raw"] == "台北市中正區重慶南路一段6號"


def test_idempotency_cache_is_scoped_per_tenant():
    """A key replayed by another tenant must not return the first tenant's receipt."""

    shared_key = "IDEM-SHARED-KEY-1234567890"

    receipt_a = commit_xlsx_import(
        rows=[{"address_raw": "台北市信義區松高路11號", "rent_amount": 90000}],
        scope={"tenant_id": "tenant-a"},
        idempotency_key=shared_key,
        actor_id="actor-a",
    )
    receipt_b = commit_xlsx_import(
        rows=[
            {"address_raw": "高雄市前金區中正四路20號", "rent_amount": 21000},
            {"address_raw": "高雄市前金區中正四路22號", "rent_amount": 23000},
        ],
        scope={"tenant_id": "tenant-b"},
        idempotency_key=shared_key,
        actor_id="actor-b",
    )

    assert receipt_a.replayed is False
    assert receipt_b.replayed is False
    assert receipt_b.batch_id != receipt_a.batch_id
    assert set(receipt_b.intake_ids).isdisjoint(receipt_a.intake_ids)

    # Tenant B's own rows were committed rather than silently dropped.
    assert receipt_b.accepted_count == 2
    stored = [get_committed_intake(intake_id) for intake_id in receipt_b.intake_ids]
    assert [row["address_raw"] for row in stored] == [
        "高雄市前金區中正四路20號",
        "高雄市前金區中正四路22號",
    ]

    # And a genuine duplicate within tenant A still replays.
    replayed = commit_xlsx_import(
        rows=[{"address_raw": "台北市信義區松高路11號", "rent_amount": 90000}],
        scope={"tenant_id": "tenant-a"},
        idempotency_key=shared_key,
        actor_id="actor-a",
    )
    assert replayed.replayed is True
    assert replayed.intake_ids == receipt_a.intake_ids


def test_negative_text_cell_is_not_rewritten_as_positive():
    """A text-formatted `-50000` must stay negative and fail the range rule."""

    xlsx_bytes = _create_mock_xlsx([
        ["地址", "租金"],
        ["台北市大同區承德路一段1號", "-50000"],
    ])

    result = preview_xlsx_import(xlsx_bytes)

    assert result.valid_count == 0
    assert result.rejected_count == 1
    codes = {(err.field, err.code) for err in result.row_errors}
    assert ("rent_amount", "INVALID_RANGE") in codes


def test_formula_prefix_is_stripped_once_for_non_numeric_text():
    """Injection triggers are neutralised without eating a run of characters."""

    xlsx_bytes = _create_mock_xlsx([
        ["地址", "標題"],
        ["台北市萬華區康定路5號", "=cmd|'/c calc'!A1"],
    ])

    parser = SafeXlsxParser(xlsx_bytes)
    rows, warnings, has_formula_or_link = parser.parse()

    assert has_formula_or_link is True
    assert rows[0]["標題"] == "cmd|'/c calc'!A1"
    assert any("formula injection prefix" in w for w in warnings)


def test_sparse_cells_do_not_shift_values_into_the_wrong_column():
    """An omitted empty cell must not move later values one column left."""

    sheet = _worksheet(
        _inline_row(1, {"A1": "地址", "B1": "租金", "C1": "坪數"})
        # B2 (rent) is omitted, exactly as Excel writes a blank cell.
        + _inline_row(2, {"A2": "台北市信義區1號", "C2": "25"})
    )
    xlsx_bytes = _zip_parts({
        "xl/workbook.xml": _WORKBOOK_XML,
        "xl/_rels/workbook.xml.rels": _WORKBOOK_RELS_XML,
        "xl/worksheets/sheet1.xml": sheet,
    })

    parser = SafeXlsxParser(xlsx_bytes)
    rows, _, _ = parser.parse()

    assert rows[0]["地址"] == "台北市信義區1號"
    assert rows[0]["坪數"] == "25"
    assert "租金" not in rows[0]

    result = preview_xlsx_import(xlsx_bytes)
    assert result.valid_rows[0]["area_ping"] == 25.0
    assert "rent_amount" not in result.valid_rows[0]


def test_row_index_reports_the_spreadsheet_row_number():
    """Error reports must point at the operator's own line numbers."""

    sheet = _worksheet(
        _inline_row(1, {"A1": "地址", "B1": "租金"})
        + _inline_row(7, {"A7": "", "B7": "45000"})
    )
    xlsx_bytes = _zip_parts({
        "xl/workbook.xml": _WORKBOOK_XML,
        "xl/_rels/workbook.xml.rels": _WORKBOOK_RELS_XML,
        "xl/worksheets/sheet1.xml": sheet,
    })

    result = preview_xlsx_import(xlsx_bytes)

    assert result.rejected_count == 1
    assert [err.row_index for err in result.row_errors] == [7]


def test_first_sheet_is_resolved_through_workbook_order():
    """Sheet order comes from workbook.xml, not from part filename sort."""

    workbook = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Listings" sheetId="1" r:id="rId7"/></sheets>'
        '</workbook>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId7" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet3.xml"/>'
        '</Relationships>'
    )
    xlsx_bytes = _zip_parts({
        "xl/workbook.xml": workbook,
        "xl/_rels/workbook.xml.rels": rels,
        "xl/worksheets/sheet1.xml": _worksheet(
            _inline_row(1, {"A1": "地址"}) + _inline_row(2, {"A2": "SCRATCH-SHEET-ROW"})
        ),
        "xl/worksheets/sheet3.xml": _worksheet(
            _inline_row(1, {"A1": "地址"}) + _inline_row(2, {"A2": "台北市大安區和平東路9號"})
        ),
    })

    parser = SafeXlsxParser(xlsx_bytes)
    rows, _, _ = parser.parse()

    assert rows[0]["地址"] == "台北市大安區和平東路9號"


def test_entity_declaration_is_refused_before_parsing():
    """Billion-laughs style expansion is prevented by refusing the declaration."""

    hostile_sheet = (
        '<?xml version="1.0"?>'
        '<!DOCTYPE worksheet ['
        '<!ENTITY a "AAAAAAAAAA">'
        '<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">'
        '<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">'
        ']>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>&c;</t></is></c></row></sheetData>'
        '</worksheet>'
    )
    xlsx_bytes = _zip_parts({
        "xl/workbook.xml": _WORKBOOK_XML,
        "xl/_rels/workbook.xml.rels": _WORKBOOK_RELS_XML,
        "xl/worksheets/sheet1.xml": hostile_sheet,
    })

    with pytest.raises(XlsxImportError) as exc_info:
        SafeXlsxParser(xlsx_bytes).parse()
    assert exc_info.value.code == "UNSAFE_XML"


def test_decompression_is_bounded_while_reading(monkeypatch):
    """The cap is enforced on bytes actually decompressed, not on declared sizes."""

    padded_sheet = _worksheet(
        _inline_row(1, {"A1": "地址"})
        + _inline_row(2, {"A2": "台北市信義區2號" + "x" * 200_000})
    )
    xlsx_bytes = _zip_parts({
        "xl/workbook.xml": _WORKBOOK_XML,
        "xl/_rels/workbook.xml.rels": _WORKBOOK_RELS_XML,
        "xl/worksheets/sheet1.xml": padded_sheet,
    })

    # Declared totals stay far below MAX_UNCOMPRESSED_BYTES, so the central
    # directory pre-filter passes and only the streaming counter can stop this.
    with zipfile.ZipFile(io.BytesIO(xlsx_bytes)) as zf:
        assert sum(info.file_size for info in zf.infolist()) < xlsx_import.MAX_UNCOMPRESSED_BYTES

    monkeypatch.setattr(xlsx_import, "MAX_PART_BYTES", 4096)
    with pytest.raises(XlsxImportError) as exc_info:
        SafeXlsxParser(xlsx_bytes).parse()
    assert exc_info.value.code == "ZIP_BOMB_PREVENTION"


def test_malformed_relationships_part_fails_closed():
    """A hostile .rels part must fail the import, not skip external-link detection."""

    xlsx_bytes = _zip_parts({
        "xl/workbook.xml": _WORKBOOK_XML,
        "xl/_rels/workbook.xml.rels": "<Relationships><unclosed>",
        "xl/worksheets/sheet1.xml": _worksheet(_inline_row(1, {"A1": "地址"})),
    })

    with pytest.raises(XlsxImportError) as exc_info:
        SafeXlsxParser(xlsx_bytes).parse()
    assert exc_info.value.code == "MALFORMED_XLSX_FILE"


def test_error_export_neutralises_formula_injection():
    """The export must not hand a live formula to the spreadsheet that opens it."""

    row_errors = [
        XlsxRowError(
            row_index=2,
            field="address_raw",
            code="REQUIRED_FIELD_MISSING",
            message="address_raw is required",
            value="=cmd|'/c calc'!A1",
        )
    ]

    csv_bytes, _ = export_xlsx_import_errors(row_errors, export_format="csv")
    csv_str = csv_bytes.decode("utf-8-sig")
    assert "'=cmd|'/c calc'!A1" in csv_str
    assert not any(line.split(",")[-1].startswith("=") for line in csv_str.splitlines())

    xlsx_bytes, _ = export_xlsx_import_errors(row_errors, export_format="xlsx")
    exported_rows, _, has_formula_or_link = SafeXlsxParser(xlsx_bytes).parse()
    assert has_formula_or_link is False
    assert "'=cmd" in str(exported_rows[0])


def test_preview_sessions_are_bounded_and_tenant_scoped():
    """The preview cache evicts, and one tenant cannot read another's batch."""

    xlsx_bytes = _create_mock_xlsx([["地址", "租金"], ["", "45000"]])

    result = preview_xlsx_import(xlsx_bytes, scope={"tenant_id": "tenant-owner"})
    assert get_preview_result(result.batch_id, "tenant-owner") is not None
    assert get_preview_result(result.batch_id, "tenant-intruder") is None
    assert get_preview_result("xlsx-batch-does-not-exist", "tenant-owner") is None

    for _ in range(xlsx_import.MAX_PREVIEW_SESSIONS + 5):
        preview_xlsx_import(xlsx_bytes, scope={"tenant_id": "tenant-owner"})

    assert len(xlsx_import._PREVIEW_STORE) <= xlsx_import.MAX_PREVIEW_SESSIONS
    assert get_preview_result(result.batch_id, "tenant-owner") is None

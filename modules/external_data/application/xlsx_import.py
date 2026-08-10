"""Governed XLSX import application module for Pantheon (ODP-CAP-XLSX-IMPORT-001).

This module implements safe parsing, schema mapping, preview validation,
idempotent commit, and sensitive masking error export for XLSX spreadsheet intake.

Deliberate boundaries & security rules:
1. Malformed formula and external-link inputs fail safely without code execution.
2. Preview performs NO writes to storage or intake state.
3. Commit writes ONLY rows that pass the same domain validation preview applies,
   through a caller-supplied writer (:data:`IntakeWriter`).
4. Duplicate commit requests with the same Idempotency-Key are idempotent, and the
   idempotency cache is bound to the committing tenant and actor.
5. Row errors exported for download are subjected to sensitive data masking (PII/secrets)
   and to spreadsheet-formula neutralisation on the way out.
6. Domain validation (URL, address, scope, ranges) is strictly enforced and never
   bypassed: :func:`commit_xlsx_import` re-runs :func:`map_and_validate_rows`, so a
   client cannot widen what preview accepted.

XML handling: parts are parsed with ``xml.etree.ElementTree`` after any part
declaring a DTD or entity is rejected outright. ElementTree expands internal
entities, so entity-expansion ("billion laughs") is prevented by refusing the
declaration rather than by bounding the expansion. Decompressed bytes are counted
as they are read from the archive, not taken from the ZIP central directory.
"""

from __future__ import annotations

import csv
import io
import json
import posixpath
import re
import uuid
import xml.etree.ElementTree as ET
import zipfile
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from modules.external_data.application.assisted_intake import (
    normalize_address,
    normalize_floor,
    resolve_source_policy,
    validate_url,
)
from shared.audit import InMemoryAuditLog

# ---------------------------------------------------------------------------
# Constants & Defaults
# ---------------------------------------------------------------------------

MAX_XLSX_BYTES = 20 * 1024 * 1024  # 20 MB max upload size
MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024  # 100 MB max decompressed budget per file
MAX_PART_BYTES = 32 * 1024 * 1024  # 32 MB max decompressed size for a single part
MAX_ZIP_ENTRIES = 500
_READ_CHUNK_BYTES = 64 * 1024

# Bounded in-process caches. Both stores are LRU-evicted so an authenticated
# actor cannot grow process memory monotonically by repeating requests.
MAX_PREVIEW_SESSIONS = 64
MAX_IDEMPOTENCY_RECORDS = 512
MAX_COMMITTED_INTAKES = 2048

DEFAULT_COLUMN_MAPPING: dict[str, str] = {
    "地址": "address_raw",
    "address": "address_raw",
    "address_raw": "address_raw",
    "租金": "rent_amount",
    "rent": "rent_amount",
    "rent_amount": "rent_amount",
    "坪數": "area_ping",
    "area": "area_ping",
    "area_ping": "area_ping",
    "樓層": "floor",
    "floor": "floor",
    "網址": "original_url",
    "url": "original_url",
    "original_url": "original_url",
    "來源": "source_id",
    "source": "source_id",
    "source_id": "source_id",
    "標題": "title",
    "title": "title",
    "型態": "listing_type",
    "type": "listing_type",
    "listing_type": "listing_type",
}

FORMULA_PREFIXES = ("=", "@", "+", "-")

# Sensitive PII regex patterns for masking
PHONE_REGEX = re.compile(r"(\b09\d{2})[-]?\d{3}[-]?(\d{3}\b)|\b(0\d{1,2})[-]?\d{3,4}[-]?(\d{4}\b)")
EMAIL_REGEX = re.compile(r"([a-zA-Z0-9_.+-]+)@([a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)")
SECRET_KEYWORD_REGEX = re.compile(r"(?:password|secret|token|apikey|bearer)\s*[:=]\s*\S+", re.IGNORECASE)

# A markup declaration in an OpenXML part is never legitimate; `<!--` (comment)
# is the only other `<!` construct and does not match this pattern.
_XML_DECLARATION_REGEX = re.compile(rb"<!\s*(?:DOCTYPE|ENTITY)", re.IGNORECASE)
_CELL_COLUMN_REGEX = re.compile(r"^([A-Za-z]+)")
_NATURAL_KEY_REGEX = re.compile(r"(\d+)")
_EXPORT_INJECTION_REGEX = re.compile(r"^[\s\x00]*[=@+\-]")

_RELATIONSHIP_ID_ATTR = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"


# ---------------------------------------------------------------------------
# Exceptions & Data Classes
# ---------------------------------------------------------------------------

class XlsxImportError(ValueError):
    """Raised when an XLSX import operation fails safely."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass
class XlsxRowError:
    row_index: int
    field: str
    code: str
    message: str
    value: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "row_index": self.row_index,
            "field": self.field,
            "code": self.code,
            "message": self.message,
            "value": str(self.value) if self.value is not None else None,
        }


@dataclass
class XlsxPreviewResult:
    batch_id: str
    total_rows: int
    valid_count: int
    rejected_count: int
    schema_mapping: dict[str, str]
    has_formula_or_external_link_warnings: bool
    warnings: list[str]
    valid_rows: list[dict[str, Any]]
    row_errors: list[XlsxRowError]
    preview_rows: list[dict[str, Any]]
    tenant_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        # ``tenant_id`` is an internal ownership binding for the preview cache and
        # is deliberately not part of the wire contract.
        return {
            "batch_id": self.batch_id,
            "total_rows": self.total_rows,
            "valid_count": self.valid_count,
            "rejected_count": self.rejected_count,
            "schema_mapping": self.schema_mapping,
            "has_formula_or_external_link_warnings": self.has_formula_or_external_link_warnings,
            "warnings": self.warnings,
            "valid_rows": self.valid_rows,
            "row_errors": [err.to_dict() for err in self.row_errors],
            "preview_rows": self.preview_rows,
        }


@dataclass
class XlsxCommitReceipt:
    batch_id: str
    committed_at: str
    accepted_count: int
    rejected_count: int
    intake_ids: list[str]
    correlation_id: str
    replayed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "committed_at": self.committed_at,
            "accepted_count": self.accepted_count,
            "rejected_count": self.rejected_count,
            "intake_ids": self.intake_ids,
            "correlation_id": self.correlation_id,
            "replayed": self.replayed,
        }


# ---------------------------------------------------------------------------
# XML / cell helpers
# ---------------------------------------------------------------------------

def _safe_xml_fromstring(data: bytes, part_name: str) -> ET.Element:
    """Parse an OpenXML part, refusing any part that declares a DTD or entity."""
    if _XML_DECLARATION_REGEX.search(data):
        raise XlsxImportError(
            "UNSAFE_XML",
            f"XML part '{part_name}' declares a DTD or entity; refused before parsing",
        )
    return ET.fromstring(data)  # nosec B314


def _column_index(cell_ref: str) -> int | None:
    """Decode the column of an A1-style cell reference (``C2`` -> 2)."""
    match = _CELL_COLUMN_REGEX.match(cell_ref or "")
    if not match:
        return None
    index = 0
    for char in match.group(1).upper():
        index = index * 26 + (ord(char) - 64)
    return index - 1


def _natural_key(name: str) -> list[Any]:
    return [int(part) if part.isdigit() else part for part in _NATURAL_KEY_REGEX.split(name)]


def _looks_numeric(text: str) -> bool:
    try:
        float(text.strip())
    except (TypeError, ValueError):
        return False
    return True


def _sanitize_cell_text(value: Any) -> tuple[Any, bool]:
    """Neutralise a leading formula trigger without corrupting numeric text.

    Returns ``(value, was_sanitized)``. A cell whose text is a valid number (for
    example a text-formatted ``-50000``) is left untouched: stripping its sign
    would defeat the negative-value domain rules downstream. Otherwise exactly one
    leading trigger character is removed, never a whole run of them.
    """
    if not isinstance(value, str) or not value.startswith(FORMULA_PREFIXES):
        return value, False
    if _looks_numeric(value):
        return value, False
    return value[1:], True


def _xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def neutralize_export_cell(value: Any) -> Any:
    """Prefix an exported cell that would be read as a formula by a spreadsheet."""
    if value is None:
        return None
    text = str(value)
    if _EXPORT_INJECTION_REGEX.match(text):
        return "'" + text
    return text


# ---------------------------------------------------------------------------
# Safe XLSX Parser Component
# ---------------------------------------------------------------------------

class SafeXlsxParser:
    """Safe, zero-dependency OpenXML XLSX reader using stdlib zipfile & ElementTree.

    Protects against:
    - Corrupt ZIP or XML files (fails closed safely)
    - Zip bombs (decompressed bytes are counted as they are read, not trusted
      from the ZIP central directory)
    - DTD / entity declarations, which are refused before parsing
    - External links (xl/externalLinks/, external target rels)
    - Formula execution & injection (flags cell formulas without executing them)
    """

    def __init__(self, file_bytes: bytes):
        if not file_bytes:
            raise XlsxImportError("MALFORMED_XLSX_FILE", "Input file buffer is empty")
        if len(file_bytes) > MAX_XLSX_BYTES:
            raise XlsxImportError("FILE_TOO_LARGE", f"File size ({len(file_bytes)} bytes) exceeds limit ({MAX_XLSX_BYTES} bytes)")
        self.file_bytes = file_bytes
        self.warnings: list[str] = []
        self.has_formula_or_external_link = False
        self._decompressed_budget = MAX_UNCOMPRESSED_BYTES

    def parse(self) -> tuple[list[dict[str, Any]], list[str], bool]:
        """Parse XLSX file into a list of row dicts (header -> value)."""
        try:
            with zipfile.ZipFile(io.BytesIO(self.file_bytes), "r") as zf:
                self._check_zip_limits(zf)
                self._check_external_links(zf)
                shared_strings = self._parse_shared_strings(zf)
                sheet_name = self._find_first_sheet(zf)
                rows = self._parse_sheet_rows(zf, sheet_name, shared_strings)
                return rows, self.warnings, self.has_formula_or_external_link
        except zipfile.BadZipFile as exc:
            raise XlsxImportError("MALFORMED_XLSX_FILE", f"Malformed or corrupt XLSX file container: {exc}") from exc
        except ET.ParseError as exc:
            raise XlsxImportError("MALFORMED_XLSX_FILE", f"Malformed XML structure within XLSX: {exc}") from exc
        except XlsxImportError:
            raise
        except Exception as exc:
            raise XlsxImportError("MALFORMED_XLSX_FILE", f"Failed to parse XLSX file safely: {exc}") from exc

    # -- archive access -----------------------------------------------------

    def _check_zip_limits(self, zf: zipfile.ZipFile) -> None:
        """Cheap pre-filter on declared metadata; the real cap is in `_read_part`."""
        infolist = zf.infolist()
        if len(infolist) > MAX_ZIP_ENTRIES:
            raise XlsxImportError("ZIP_BOMB_PREVENTION", f"XLSX entry count ({len(infolist)}) exceeds maximum permitted ({MAX_ZIP_ENTRIES})")
        declared_size = sum(info.file_size for info in infolist)
        if declared_size > MAX_UNCOMPRESSED_BYTES:
            raise XlsxImportError("ZIP_BOMB_PREVENTION", f"Declared uncompressed size ({declared_size} bytes) exceeds limit ({MAX_UNCOMPRESSED_BYTES} bytes)")

    def _read_part(self, zf: zipfile.ZipFile, name: str) -> bytes:
        """Read one archive member, bounding the bytes actually decompressed.

        ``ZipInfo.file_size`` is written by whoever built the archive, so it is
        only a pre-filter. This counts what decompression really produces and
        aborts as soon as either the per-part or the per-file budget is passed.
        """
        buffer = bytearray()
        with zf.open(name, "r") as handle:
            while True:
                chunk = handle.read(_READ_CHUNK_BYTES)
                if not chunk:
                    break
                buffer.extend(chunk)
                self._decompressed_budget -= len(chunk)
                if len(buffer) > MAX_PART_BYTES:
                    raise XlsxImportError(
                        "ZIP_BOMB_PREVENTION",
                        f"Decompressed part '{name}' exceeds per-part limit ({MAX_PART_BYTES} bytes)",
                    )
                if self._decompressed_budget < 0:
                    raise XlsxImportError(
                        "ZIP_BOMB_PREVENTION",
                        f"Decompressed size exceeds limit ({MAX_UNCOMPRESSED_BYTES} bytes)",
                    )
        return bytes(buffer)

    def _read_relationships(self, zf: zipfile.ZipFile, name: str) -> dict[str, tuple[str, str]]:
        """Return ``{relationship_id: (target, target_mode)}`` for a .rels part."""
        tree = _safe_xml_fromstring(self._read_part(zf, name), name)
        relationships: dict[str, tuple[str, str]] = {}
        for elem in tree.findall("{*}Relationship"):
            rel_id = elem.attrib.get("Id", "")
            relationships[rel_id] = (
                elem.attrib.get("Target", ""),
                elem.attrib.get("TargetMode", ""),
            )
        return relationships

    def _check_external_links(self, zf: zipfile.ZipFile) -> None:
        namelist = zf.namelist()
        external_link_files = [name for name in namelist if "externalLinks" in name]
        if external_link_files:
            self.has_formula_or_external_link = True
            self.warnings.append(f"Detected external link references: {', '.join(external_link_files)}")

        # Check relationships for external targets. A malformed or hostile .rels
        # part must fail the import, not silently disable the check: `parse()`
        # turns the resulting error into a clean MALFORMED_XLSX_FILE failure.
        for name in namelist:
            if not name.endswith(".rels"):
                continue
            for target, target_mode in self._read_relationships(zf, name).values():
                if target_mode.lower() == "external" or target.startswith(("http://", "https://", "ftp://", "file://")):
                    self.has_formula_or_external_link = True
                    self.warnings.append(f"External target relationship detected in {name}: {target}")

    def _parse_shared_strings(self, zf: zipfile.ZipFile) -> list[str]:
        if "xl/sharedStrings.xml" not in zf.namelist():
            return []
        tree = _safe_xml_fromstring(self._read_part(zf, "xl/sharedStrings.xml"), "xl/sharedStrings.xml")
        strings = []
        for si in tree.findall("{*}si"):
            # A shared string can be simple <t> text or formatted <r><t> runs
            texts = [t.text or "" for t in si.findall(".//{*}t")]
            strings.append("".join(texts))
        return strings

    def _find_first_sheet(self, zf: zipfile.ZipFile) -> str:
        """Resolve the workbook's first sheet through workbook.xml + its rels.

        Part filenames carry no ordering guarantee — ``xl/worksheets/sheet1.xml``
        is often a leftover scratch sheet — so sheet order is read from
        ``xl/workbook.xml`` and resolved via its relationship part. The
        filename sort is only a fallback for workbooks missing those parts.
        """
        namelist = zf.namelist()
        resolved = self._resolve_workbook_first_sheet(zf, namelist)
        if resolved is not None:
            return resolved

        sheet_files = [n for n in namelist if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")]
        if not sheet_files:
            raise XlsxImportError("EMPTY_WORKBOOK", "No worksheet files found in XLSX archive")
        sheet_files.sort(key=_natural_key)
        return sheet_files[0]

    def _resolve_workbook_first_sheet(self, zf: zipfile.ZipFile, namelist: list[str]) -> str | None:
        if "xl/workbook.xml" not in namelist or "xl/_rels/workbook.xml.rels" not in namelist:
            return None
        workbook = _safe_xml_fromstring(self._read_part(zf, "xl/workbook.xml"), "xl/workbook.xml")
        sheets = workbook.find("{*}sheets")
        if sheets is None:
            return None
        first_sheet = sheets.find("{*}sheet")
        if first_sheet is None:
            return None
        rel_id = first_sheet.attrib.get(_RELATIONSHIP_ID_ATTR)
        if not rel_id:
            return None
        relationship = self._read_relationships(zf, "xl/_rels/workbook.xml.rels").get(rel_id)
        if not relationship:
            return None
        target = relationship[0]
        if not target:
            return None
        if target.startswith("/"):
            candidate = target.lstrip("/")
        else:
            candidate = posixpath.normpath(posixpath.join("xl", target))
        return candidate if candidate in namelist else None

    # -- sheet decoding -----------------------------------------------------

    def _parse_sheet_rows(self, zf: zipfile.ZipFile, sheet_name: str, shared_strings: list[str]) -> list[dict[str, Any]]:
        tree = _safe_xml_fromstring(self._read_part(zf, sheet_name), sheet_name)
        sheet_data = tree.find("{*}sheetData")
        if sheet_data is None:
            return []

        # (spreadsheet row number, {column index: (value, has_formula)}). Cells are
        # placed by their decoded `r` reference: OpenXML writers omit `<c>` for
        # empty cells, so positional reading shifts every later value one column.
        raw_grid: list[tuple[int, dict[int, tuple[Any, bool]]]] = []
        next_row_number = 1
        for row_elem in sheet_data.findall("{*}row"):
            cells: dict[int, tuple[Any, bool]] = {}
            next_col = 0
            for cell_elem in row_elem.findall("{*}c"):
                col_idx = _column_index(cell_elem.attrib.get("r", ""))
                if col_idx is None:
                    col_idx = next_col
                next_col = col_idx + 1
                cells[col_idx] = self._parse_cell_value(cell_elem, shared_strings)

            try:
                row_number = int(row_elem.attrib.get("r", ""))
            except (TypeError, ValueError):
                row_number = next_row_number
            next_row_number = row_number + 1
            raw_grid.append((row_number, cells))

        if not raw_grid:
            return []

        header_by_col: dict[int, str] = {}
        for col_idx, (value, _has_formula) in raw_grid[0][1].items():
            header = str(value).strip()
            if header:
                header_by_col[col_idx] = header

        rows: list[dict[str, Any]] = []
        for row_number, cells in raw_grid[1:]:
            row_dict: dict[str, Any] = {"_row_index": row_number}
            for col_idx in sorted(cells):
                value, has_formula = cells[col_idx]
                header = header_by_col.get(col_idx) or f"Column_{col_idx + 1}"
                sanitized, was_sanitized = _sanitize_cell_text(value)

                if has_formula:
                    self.has_formula_or_external_link = True
                    self.warnings.append(f"Row {row_number} column '{header}' contained a formula (not executed)")
                    row_dict[header] = sanitized
                    row_dict[f"_formula_warning_{header}"] = True
                elif was_sanitized:
                    self.has_formula_or_external_link = True
                    self.warnings.append(f"Row {row_number} column '{header}' contains potential formula injection prefix")
                    row_dict[header] = sanitized
                    row_dict[f"_formula_warning_{header}"] = True
                else:
                    row_dict[header] = value

            rows.append(row_dict)

        return rows

    def _parse_cell_value(self, cell_elem: ET.Element, shared_strings: list[str]) -> tuple[Any, bool]:
        t_attr = cell_elem.attrib.get("t", "")
        formula_elem = cell_elem.find("{*}f")
        has_formula = formula_elem is not None

        if has_formula:
            # Check formula text for external references
            f_text = (formula_elem.text or "").strip() if formula_elem is not None else ""
            if any(token in f_text.upper() for token in ["HYPERLINK", "CMD", "EXEC", "DDE", "[", "]"]):
                self.has_formula_or_external_link = True
                self.warnings.append(f"Formula contains external reference or dynamic function: {f_text}")

        v_elem = cell_elem.find("{*}v")
        is_elem = cell_elem.find("{*}is")

        val_str = ""
        if v_elem is not None and v_elem.text is not None:
            val_str = v_elem.text
        elif is_elem is not None:
            texts = [t.text or "" for t in is_elem.findall(".//{*}t")]
            val_str = "".join(texts)

        if t_attr == "s":  # Shared string reference
            try:
                idx = int(val_str)
                return (shared_strings[idx] if 0 <= idx < len(shared_strings) else val_str), has_formula
            except (ValueError, TypeError):
                return val_str, has_formula
        elif t_attr == "b":  # Boolean
            return (val_str == "1"), has_formula
        elif t_attr == "n" or not t_attr:  # Numeric or untyped
            if val_str:
                try:
                    if "." in val_str:
                        return float(val_str), has_formula
                    return int(val_str), has_formula
                except ValueError:
                    return val_str, has_formula
            return "", has_formula
        else:
            return val_str, has_formula


# ---------------------------------------------------------------------------
# Sensitive Data Masker Component
# ---------------------------------------------------------------------------

def mask_sensitive_value(val: Any) -> Any:
    """Mask PII, phone numbers, email addresses, and secret keys in error values."""
    if val is None:
        return None
    s = str(val)
    if not s:
        return s

    # Mask secrets
    s = SECRET_KEYWORD_REGEX.sub("[RESTRICTED_SECRET]", s)

    # Mask emails
    def _mask_email(m: re.Match) -> str:
        name, domain = m.group(1), m.group(2)
        if len(name) <= 2:
            masked_name = name[0] + "*"
        else:
            masked_name = name[0] + "***" + name[-1]
        return f"{masked_name}@{domain}"

    s = EMAIL_REGEX.sub(_mask_email, s)

    # Mask phone numbers
    def _mask_phone(m: re.Match) -> str:
        full = m.group(0)
        digits_only = re.sub(r"\D", "", full)
        if len(digits_only) >= 9:
            return digits_only[:4] + "****" + digits_only[-2:]
        return digits_only[:2] + "***"

    s = PHONE_REGEX.sub(_mask_phone, s)
    return s


# ---------------------------------------------------------------------------
# Schema Mapping & Domain Validation Component
# ---------------------------------------------------------------------------

def map_and_validate_rows(
    raw_rows: list[dict[str, Any]],
    custom_mapping: dict[str, str] | None = None,
) -> tuple[dict[str, str], list[dict[str, Any]], list[XlsxRowError]]:
    """Map raw XLSX columns to domain target schema and perform domain validation."""

    mapping = dict(DEFAULT_COLUMN_MAPPING)
    if custom_mapping:
        mapping.update(custom_mapping)

    valid_rows: list[dict[str, Any]] = []
    row_errors: list[XlsxRowError] = []

    for position, row in enumerate(raw_rows, start=1):
        row_idx = row.get("_row_index", position)
        mapped_row: dict[str, Any] = {"_row_index": row_idx}
        has_error = False

        # Apply schema column mapping
        for key, val in row.items():
            if key.startswith(("_row_index", "_formula_warning_")):
                continue
            normalized_key = key.strip().lower()
            target_field = mapping.get(key) or mapping.get(normalized_key) or normalized_key
            mapped_row[target_field] = val

            if row.get(f"_formula_warning_{key}"):
                row_errors.append(
                    XlsxRowError(
                        row_index=row_idx,
                        field=target_field,
                        code="FORMULA_WARNING",
                        message="Cell contained formula or formula injection prefix (sanitized)",
                        value=mask_sensitive_value(val),
                    )
                )

        # Domain Validation Rules
        # Rule 1: Required address_raw
        address_raw = str(mapped_row.get("address_raw") or "").strip()
        if not address_raw:
            row_errors.append(
                XlsxRowError(
                    row_index=row_idx,
                    field="address_raw",
                    code="REQUIRED_FIELD_MISSING",
                    message="address_raw is required and cannot be empty",
                    value=None,
                )
            )
            has_error = True
        else:
            mapped_row["normalized_address"] = normalize_address(address_raw)

        # Rule 2: Numeric rent_amount validation
        rent_val = mapped_row.get("rent_amount")
        if rent_val not in (None, ""):
            try:
                rent_float = float(rent_val)
                if rent_float < 0:
                    row_errors.append(
                        XlsxRowError(
                            row_index=row_idx,
                            field="rent_amount",
                            code="INVALID_RANGE",
                            message="rent_amount cannot be negative",
                            value=mask_sensitive_value(rent_val),
                        )
                    )
                    has_error = True
                else:
                    mapped_row["rent_amount"] = rent_float
            except (ValueError, TypeError):
                row_errors.append(
                    XlsxRowError(
                        row_index=row_idx,
                        field="rent_amount",
                        code="INVALID_NUMERIC",
                        message="rent_amount must be a valid number",
                        value=mask_sensitive_value(rent_val),
                    )
                )
                has_error = True

        # Rule 3: Numeric area_ping validation
        area_val = mapped_row.get("area_ping")
        if area_val not in (None, ""):
            try:
                area_float = float(area_val)
                if area_float < 0:
                    row_errors.append(
                        XlsxRowError(
                            row_index=row_idx,
                            field="area_ping",
                            code="INVALID_RANGE",
                            message="area_ping cannot be negative",
                            value=mask_sensitive_value(area_val),
                        )
                    )
                    has_error = True
                else:
                    mapped_row["area_ping"] = area_float
            except (ValueError, TypeError):
                row_errors.append(
                    XlsxRowError(
                        row_index=row_idx,
                        field="area_ping",
                        code="INVALID_NUMERIC",
                        message="area_ping must be a valid number",
                        value=mask_sensitive_value(area_val),
                    )
                )
                has_error = True

        # Rule 4: Floor normalization
        if mapped_row.get("floor"):
            mapped_row["normalized_floor"] = normalize_floor(str(mapped_row["floor"]))

        # Rule 5: URL validation & policy check if original_url provided
        orig_url = str(mapped_row.get("original_url") or "").strip()
        if orig_url:
            try:
                valid_url = validate_url(orig_url)
                policy_decision = resolve_source_policy(valid_url)
                mapped_row["original_url"] = valid_url
                mapped_row["policy_state"] = policy_decision.policy
                if policy_decision.quarantines:
                    row_errors.append(
                        XlsxRowError(
                            row_index=row_idx,
                            field="original_url",
                            code="URL_POLICY_QUARANTINED",
                            message=f"Source URL policy quarantined: {policy_decision.policy_reason}",
                            value=mask_sensitive_value(orig_url),
                        )
                    )
                    has_error = True
            except Exception as exc:
                row_errors.append(
                    XlsxRowError(
                        row_index=row_idx,
                        field="original_url",
                        code="INVALID_URL",
                        message=f"URL domain validation failed: {exc}",
                        value=mask_sensitive_value(orig_url),
                    )
                )
                has_error = True

        if not has_error:
            valid_rows.append(mapped_row)

    return mapping, valid_rows, row_errors


# ---------------------------------------------------------------------------
# Core Application Functions: Preview, Commit, Error Export
# ---------------------------------------------------------------------------

#: Persists one validated row and returns the intake id it was written under.
IntakeWriter = Callable[[dict[str, Any]], str]

# Bounded in-memory stores for preview sessions, commit idempotency, and the
# default (no-writer) commit target.
_PREVIEW_STORE: OrderedDict[str, XlsxPreviewResult] = OrderedDict()
_IDEMPOTENCY_STORE: OrderedDict[str, XlsxCommitReceipt] = OrderedDict()
_COMMITTED_INTAKES: OrderedDict[str, dict[str, Any]] = OrderedDict()


def _remember(store: OrderedDict[str, Any], key: str, value: Any, max_entries: int) -> None:
    store[key] = value
    store.move_to_end(key)
    while len(store) > max_entries:
        store.popitem(last=False)


def _tenant_of(scope: dict[str, Any] | None) -> str | None:
    tenant = (scope or {}).get("tenant_id")
    return str(tenant) if tenant else None


def _idempotency_cache_key(
    idempotency_key: str | None,
    scope: dict[str, Any] | None,
    actor_id: str,
) -> str | None:
    """Bind an idempotency key to its tenant and actor.

    An unqualified key is a cross-tenant leak: tenant B reusing tenant A's key
    would receive A's batch id and intake ids while B's own rows are dropped.
    """
    if not idempotency_key:
        return None
    return f"{_tenant_of(scope) or '_unscoped'}|{actor_id}|{idempotency_key}"


def get_preview_result(batch_id: str, tenant_id: str | None = None) -> XlsxPreviewResult | None:
    """Look up a cached preview, enforcing tenant ownership.

    A caller that supplies ``tenant_id`` only ever sees previews recorded for that
    tenant; unknown and foreign batch ids are indistinguishable to it.
    """
    result = _PREVIEW_STORE.get(batch_id)
    if result is None:
        return None
    if tenant_id is not None and result.tenant_id != tenant_id:
        return None
    return result


def get_committed_intake(intake_id: str) -> dict[str, Any] | None:
    """Return a row committed through the default in-module writer."""
    return _COMMITTED_INTAKES.get(intake_id)


def _default_intake_writer(row: dict[str, Any]) -> str:
    intake_id = str(uuid.uuid4())
    _remember(_COMMITTED_INTAKES, intake_id, dict(row), MAX_COMMITTED_INTAKES)
    return intake_id


def preview_xlsx_import(
    file_bytes: bytes,
    custom_mapping: dict[str, str] | None = None,
    scope: dict[str, Any] | None = None,
) -> XlsxPreviewResult:
    """Perform safe XLSX preview validation without performing any writes.

    Guarantees:
    - Parses file safely (fails closed on malformed XML/ZIP or malicious formulas).
    - Map columns & validates domain constraints.
    - Zero writes to storage/database/intake state: the only state produced is the
      bounded, tenant-bound preview session cache read back by the error export.
    """
    parser = SafeXlsxParser(file_bytes)
    raw_rows, parser_warnings, has_formula_or_link = parser.parse()

    schema_mapping, valid_rows, row_errors = map_and_validate_rows(raw_rows, custom_mapping)

    batch_id = f"xlsx-batch-{uuid.uuid4()}"
    result = XlsxPreviewResult(
        batch_id=batch_id,
        total_rows=len(raw_rows),
        valid_count=len(valid_rows),
        rejected_count=len(raw_rows) - len(valid_rows),
        schema_mapping=schema_mapping,
        has_formula_or_external_link_warnings=has_formula_or_link,
        warnings=parser_warnings,
        valid_rows=valid_rows,
        row_errors=row_errors,
        preview_rows=valid_rows[:20],  # Sample first 20 valid rows
        tenant_id=_tenant_of(scope),
    )

    _remember(_PREVIEW_STORE, batch_id, result, MAX_PREVIEW_SESSIONS)
    return result


def commit_xlsx_import(
    rows: list[dict[str, Any]],
    batch_id: str | None = None,
    scope: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
    actor_id: str = "system.worker",
    audit_log: InMemoryAuditLog | None = None,
    correlation_id: str | None = None,
    custom_mapping: dict[str, str] | None = None,
    intake_writer: IntakeWriter | None = None,
) -> XlsxCommitReceipt:
    """Commit validated rows idempotently. Writes ONLY validated rows.

    Guarantees:
    - Re-runs the full preview validation (``map_and_validate_rows``) over the
      submitted rows, so a client cannot hand in rows that preview would reject:
      URL policy, address, and range rules apply identically on both paths.
    - Every accepted row is handed to ``intake_writer`` and the returned intake id
      is what the receipt reports, so a receipt id always resolves to a record.
    - Duplicate calls with the same tenant + actor + idempotency_key return a
      replayed receipt; a key from another tenant never matches.
    - Emits audit log record for governance.
    """
    corr_id = correlation_id or str(uuid.uuid4())

    cache_key = _idempotency_cache_key(idempotency_key, scope, actor_id)
    if cache_key is not None and cache_key in _IDEMPOTENCY_STORE:
        cached = _IDEMPOTENCY_STORE[cache_key]
        _IDEMPOTENCY_STORE.move_to_end(cache_key)
        return XlsxCommitReceipt(
            batch_id=cached.batch_id,
            committed_at=cached.committed_at,
            accepted_count=cached.accepted_count,
            rejected_count=cached.rejected_count,
            intake_ids=list(cached.intake_ids),
            correlation_id=corr_id,
            replayed=True,
        )

    # Re-run domain validation rather than a weaker subset of it: this path is
    # reachable with client-supplied rows, so preview must not be advisory.
    _mapping, valid_to_commit, row_errors = map_and_validate_rows(rows, custom_mapping)
    rejected_count = len(rows) - len(valid_to_commit)

    writer = intake_writer or _default_intake_writer
    ts = datetime.now(UTC).isoformat()
    committed_intake_ids = [writer(row) for row in valid_to_commit]

    receipt = XlsxCommitReceipt(
        batch_id=batch_id or f"xlsx-commit-{uuid.uuid4()}",
        committed_at=ts,
        accepted_count=len(valid_to_commit),
        rejected_count=rejected_count,
        intake_ids=committed_intake_ids,
        correlation_id=corr_id,
        replayed=False,
    )

    if cache_key is not None:
        _remember(_IDEMPOTENCY_STORE, cache_key, receipt, MAX_IDEMPOTENCY_RECORDS)

    if audit_log:
        from shared.audit.events import AuditEvent
        audit_log.record(
            AuditEvent(
                event_type="xlsx_import",
                actor=actor_id,
                action="xlsx_import_commit",
                resource=receipt.batch_id,
                outcome="SUCCEEDED",
                correlation_id=corr_id,
                metadata={
                    "accepted_count": receipt.accepted_count,
                    "rejected_count": receipt.rejected_count,
                    "row_error_count": len(row_errors),
                    "scope": scope or {},
                },
            )
        )

    return receipt


def export_xlsx_import_errors(
    row_errors: list[XlsxRowError],
    export_format: Literal["xlsx", "csv", "json"] = "csv",
) -> tuple[bytes, str]:
    """Export row errors with sensitive data masking applied.

    Values are masked and then neutralised for spreadsheet consumption: the export
    is served as an attachment, so a leading ``=`` in imported data would otherwise
    become a live formula in the operator's spreadsheet.

    Returns (bytes, content_type).
    """
    masked_errors = []
    for err in row_errors:
        masked_errors.append({
            "row_index": err.row_index,
            "field": str(neutralize_export_cell(mask_sensitive_value(err.field))),
            "code": str(neutralize_export_cell(err.code)),
            "message": str(neutralize_export_cell(mask_sensitive_value(err.message))),
            "value": neutralize_export_cell(mask_sensitive_value(err.value)),
        })

    if export_format == "json":
        data = json.dumps(masked_errors, indent=2, ensure_ascii=False).encode("utf-8")
        return data, "application/json"

    elif export_format == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Row Index", "Field", "Error Code", "Error Message", "Masked Value"])
        for err in masked_errors:
            writer.writerow([
                err["row_index"],
                err["field"],
                err["code"],
                err["message"],
                err["value"] if err["value"] is not None else "",
            ])
        return output.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8"

    elif export_format == "xlsx":
        # Build clean XML-based XLSX zip file in-memory
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            # Write minimal OpenXML content structures
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
            zf.writestr("_rels/.rels", (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                '</Relationships>'
            ))
            zf.writestr("xl/workbook.xml", (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                '<sheets><sheet name="Import Errors" sheetId="1" r:id="rId1"/></sheets>'
                '</workbook>'
            ))
            zf.writestr("xl/_rels/workbook.xml.rels", (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
                '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>'
                '</Relationships>'
            ))

            # Build Shared Strings Table
            strings: list[str] = ["Row Index", "Field", "Error Code", "Error Message", "Masked Value"]
            string_map: dict[str, int] = {s: i for i, s in enumerate(strings)}

            def _get_string_idx(text: str) -> int:
                if text in string_map:
                    return string_map[text]
                idx = len(strings)
                strings.append(text)
                string_map[text] = idx
                return idx

            # Construct Sheet Cells
            sheet_rows = []
            # Header Row
            sheet_rows.append(
                '<row r="1">' + "".join(f'<c r="{chr(65+i)}1" t="s"><v>{i}</v></c>' for i in range(5)) + '</row>'
            )

            for r_idx, err in enumerate(masked_errors, start=2):
                c0 = f'<c r="A{r_idx}"><v>{err["row_index"]}</v></c>'
                c1 = f'<c r="B{r_idx}" t="s"><v>{_get_string_idx(str(err["field"]))}</v></c>'
                c2 = f'<c r="C{r_idx}" t="s"><v>{_get_string_idx(str(err["code"]))}</v></c>'
                c3 = f'<c r="D{r_idx}" t="s"><v>{_get_string_idx(str(err["message"]))}</v></c>'
                c4 = f'<c r="E{r_idx}" t="s"><v>{_get_string_idx(str(err["value"] or ""))}</v></c>'
                sheet_rows.append(f'<row r="{r_idx}">{c0}{c1}{c2}{c3}{c4}</row>')

            sheet_xml = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                '<sheetData>' + "".join(sheet_rows) + '</sheetData>'
                '</worksheet>'
            )
            zf.writestr("xl/worksheets/sheet1.xml", sheet_xml)

            sst_xml = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                f'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="{len(strings)}" uniqueCount="{len(strings)}">'
                + "".join(f"<si><t>{_xml_escape(s)}</t></si>" for s in strings)
                + '</sst>'
            )
            zf.writestr("xl/sharedStrings.xml", sst_xml)

        buf.seek(0)
        return buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        raise ValueError(f"Unsupported export format: {export_format}")

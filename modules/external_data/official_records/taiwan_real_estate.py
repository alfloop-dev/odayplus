"""Official Taiwan real-estate open-data record contract and parser.

Relocated from ``modules.external_data.providers.taiwan_real_estate`` by
XR-CUTOVER-001, which decommissioned odayplus-side external ingestion. The
``mof_moi`` domain is now ingested by ``oday-data-platform``, so the
``OfficialRealEstateDownloader`` — the only part of the old module that opened
a connection to ``plvr.land.moi.gov.tw`` or ``data.ntpc.gov.tw`` — did not move
with it. There is no HTTP client and no credential here.

What remains is the record contract odayplus still owns: the approved-source
registry and its licence binding, the transaction schema, the deterministic
identity rules, and the bounded parser that turns an artifact this repository
already holds into a :class:`ParsedOfficialRealEstateBatch`. Callers that need
an artifact must supply one; nothing in this module can fetch one.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import unicodedata
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID, uuid5

GOVERNMENT_OPEN_DATA_LICENSE_V1 = "government-open-data-license-v1"
PARSER_VERSION = "tw-real-estate-outcomes-v1"
MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024
MAX_EXPANDED_BYTES = 250 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 256
MAX_ROWS = 250_000

MOI_SOURCE_URL = "https://plvr.land.moi.gov.tw/opendata/lvr_landAcsv.zip"
NTPC_SOURCE_URL = (
    "https://data.ntpc.gov.tw/api/datasets/acce802d-58cc-4dff-9e7a-9ecc517f78be/csv/file"
)

MOI_HEADERS = (
    "鄉鎮市區",
    "交易標的",
    "土地位置建物門牌",
    "土地移轉總面積平方公尺",
    "都市土地使用分區",
    "非都市土地使用分區",
    "非都市土地使用編定",
    "交易年月日",
    "交易筆棟數",
    "移轉層次",
    "總樓層數",
    "建物型態",
    "主要用途",
    "主要建材",
    "建築完成年月",
    "建物移轉總面積平方公尺",
    "建物現況格局-房",
    "建物現況格局-廳",
    "建物現況格局-衛",
    "建物現況格局-隔間",
    "有無管理組織",
    "總價元",
    "單價元平方公尺",
    "車位類別",
    "車位移轉總面積平方公尺",
    "車位總價元",
    "備註",
    "編號",
    "主建物面積",
    "附屬建物面積",
    "陽台面積",
    "電梯",
    "移轉編號",
)

NTPC_HEADERS = (
    "district",
    "rps01",
    "rps02",
    "rps03_area",
    "rps04",
    "rps05",
    "rps06",
    "rps07_yyymmddroc",
    "rps08",
    "rps09",
    "rps10",
    "rps11",
    "rps12",
    "rps13",
    "rps14_yyymmddroc",
    "rps15_area",
    "rps16_quantity",
    "rps17_quantity",
    "rps18_quantity",
    "rps19",
    "rps20",
    "rps21_amountsunitdollars",
    "rps22_amountsunitdollars",
    "rps23",
    "rps24_area",
    "rps25_amountsunitdollars",
    "rps26",
    "rps27",
    "rps28_area",
    "rps29_area",
    "rps30_area",
    "rps31",
    "rps32",
)

_TRANSACTION_NAMESPACE = UUID("80ec6e2b-d9e7-48f9-b317-00aa49a4b726")
_RUN_NAMESPACE = UUID("a649642f-ee6d-44ae-bb9a-d4156e1e77ab")
_MOI_MAIN_FILE = re.compile(r"^[a-z]_lvr_land_a\.csv$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class OfficialRealEstateSourceError(RuntimeError):
    """Raised when an official source cannot satisfy its bound contract."""


class OfficialRealEstateSchemaDrift(OfficialRealEstateSourceError):
    """Raised when an official CSV or ZIP schema changes unexpectedly."""


class OfficialRealEstateBoundExceeded(OfficialRealEstateSourceError):
    """Raised before an official source can exceed configured resource bounds."""


@dataclass(frozen=True)
class OfficialSource:
    key: str
    source_id: str
    dataset_id: str
    source_url: str
    metadata_url: str
    publisher: str
    license_id: str
    media_type: str
    municipality: str | None = None

    def validate_binding(
        self,
        *,
        dataset_id: str,
        license_id: str,
        source_url: str | None = None,
    ) -> None:
        if dataset_id != self.dataset_id:
            raise OfficialRealEstateSourceError(
                f"{self.key}: dataset mismatch; expected {self.dataset_id}"
            )
        if license_id != self.license_id:
            raise OfficialRealEstateSourceError(
                f"{self.key}: license mismatch; expected {self.license_id}"
            )
        if source_url is not None and source_url != self.source_url:
            raise OfficialRealEstateSourceError(
                f"{self.key}: source URL does not match the approved endpoint"
            )


SOURCES: Mapping[str, OfficialSource] = {
    "moi": OfficialSource(
        key="moi",
        source_id="tw_moi_lvr_land_a",
        dataset_id="25119",
        source_url=MOI_SOURCE_URL,
        metadata_url="https://data.gov.tw/dataset/25119",
        publisher="Ministry of the Interior, Department of Land Administration",
        license_id=GOVERNMENT_OPEN_DATA_LICENSE_V1,
        media_type="application/zip",
    ),
    "ntpc": OfficialSource(
        key="ntpc",
        source_id="tw_ntpc_real_estate_sales",
        dataset_id="acce802d-58cc-4dff-9e7a-9ecc517f78be",
        source_url=NTPC_SOURCE_URL,
        metadata_url=("https://data.ntpc.gov.tw/datasets/acce802d-58cc-4dff-9e7a-9ecc517f78be"),
        publisher="New Taipei City Government Land Administration Department",
        license_id=GOVERNMENT_OPEN_DATA_LICENSE_V1,
        media_type="text/csv",
        municipality="新北市",
    ),
}


@dataclass(frozen=True)
class DownloadedArtifact:
    source: OfficialSource
    content: bytes
    content_sha256: str
    content_type: str
    fetched_at: datetime
    final_url: str
    etag: str | None = None
    source_published_at: datetime | None = None


@dataclass(frozen=True)
class OfficialRealEstateTransaction:
    transaction_id: UUID
    source_id: str
    authority_partition: str
    source_record_id: str
    source_variant_id: str
    identity_fingerprint: str
    municipality: str
    district: str
    transaction_target: str
    address: str | None
    transaction_date: date
    transaction_count_summary: str | None
    land_area_sqm: Decimal | None
    urban_land_use: str | None
    non_urban_land_use: str | None
    non_urban_land_designation: str | None
    transferred_floor: str | None
    total_floors: str | None
    building_type: str | None
    main_use: str | None
    main_material: str | None
    completion_date: date | None
    completion_year: int | None
    completion_month: int | None
    building_area_sqm: Decimal | None
    room_count: int | None
    hall_count: int | None
    bathroom_count: int | None
    has_partition: bool | None
    has_management: bool | None
    total_price_twd: int
    unit_price_twd_sqm: int | None
    parking_type: str | None
    parking_area_sqm: Decimal | None
    parking_price_twd: int | None
    remarks: str | None
    main_building_area_sqm: Decimal | None
    auxiliary_building_area_sqm: Decimal | None
    balcony_area_sqm: Decimal | None
    has_elevator: bool | None
    transfer_number: str | None
    source_file: str
    source_row_number: int
    raw_record_sha256: str
    raw_fields: Mapping[str, str]


@dataclass(frozen=True)
class ParsedOfficialRealEstateBatch:
    artifact: DownloadedArtifact
    run_id: UUID
    schema_sha256: str
    records: tuple[OfficialRealEstateTransaction, ...]

    @property
    def source_snapshot_id(self) -> str:
        return f"{self.artifact.source.source_id}:sha256:{self.artifact.content_sha256}"


def parse_official_real_estate(
    artifact: DownloadedArtifact,
    *,
    max_rows: int = MAX_ROWS,
    max_expanded_bytes: int = MAX_EXPANDED_BYTES,
) -> ParsedOfficialRealEstateBatch:
    _require_approved_source(artifact.source)
    if not 1 <= max_rows <= MAX_ROWS:
        raise OfficialRealEstateBoundExceeded(f"max_rows must be between 1 and {MAX_ROWS}")
    if not 1 <= max_expanded_bytes <= MAX_EXPANDED_BYTES:
        raise OfficialRealEstateBoundExceeded(
            f"max_expanded_bytes must be between 1 and {MAX_EXPANDED_BYTES}"
        )
    artifact.source.validate_binding(
        dataset_id=artifact.source.dataset_id,
        license_id=artifact.source.license_id,
        source_url=artifact.final_url,
    )
    if artifact.content_type != artifact.source.media_type:
        raise OfficialRealEstateSourceError(
            f"{artifact.source.key}: artifact media type does not match approved source"
        )
    if hashlib.sha256(artifact.content).hexdigest() != artifact.content_sha256:
        raise OfficialRealEstateSourceError("artifact bytes do not match content_sha256")
    if artifact.source.key == "moi":
        records, schema_sha256 = _parse_moi_zip(
            artifact,
            max_rows=max_rows,
            max_expanded_bytes=max_expanded_bytes,
        )
    elif artifact.source.key == "ntpc":
        records, schema_sha256 = _parse_ntpc_csv(
            artifact,
            max_rows=max_rows,
            max_expanded_bytes=max_expanded_bytes,
        )
    else:
        raise OfficialRealEstateSourceError(f"unsupported official source {artifact.source.key!r}")
    run_id = uuid5(
        _RUN_NAMESPACE,
        f"{artifact.source.source_id}:{artifact.content_sha256}:{PARSER_VERSION}",
    )
    return ParsedOfficialRealEstateBatch(
        artifact=artifact,
        run_id=run_id,
        schema_sha256=schema_sha256,
        records=tuple(records),
    )


def _parse_moi_zip(
    artifact: DownloadedArtifact,
    *,
    max_rows: int,
    max_expanded_bytes: int,
) -> tuple[list[OfficialRealEstateTransaction], str]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(artifact.content), "r")
    except zipfile.BadZipFile as exc:
        raise OfficialRealEstateSourceError("MOI payload is not a valid ZIP archive") from exc
    with archive:
        members = archive.infolist()
        if len(members) > MAX_ARCHIVE_MEMBERS:
            raise OfficialRealEstateBoundExceeded("MOI archive member count exceeds limit")
        member_names = {member.filename for member in members}
        required = {"manifest.csv", "schema-main.csv"}
        if not required.issubset(member_names):
            raise OfficialRealEstateSchemaDrift(
                "MOI archive is missing manifest.csv or schema-main.csv"
            )
        for member in members:
            if member.flag_bits & 0x1:
                raise OfficialRealEstateSourceError("encrypted ZIP members are not allowed")
            if member.file_size > max_expanded_bytes:
                raise OfficialRealEstateBoundExceeded(
                    f"MOI archive member {member.filename} exceeds expanded-byte limit"
                )

        manifest_bytes = archive.read("manifest.csv")
        schema_bytes = archive.read("schema-main.csv")
        if len(manifest_bytes) + len(schema_bytes) > max_expanded_bytes:
            raise OfficialRealEstateBoundExceeded("MOI metadata exceeds expanded-byte limit")
        _validate_moi_schema(schema_bytes)
        main_files = _moi_main_files(manifest_bytes, member_names)
        expanded_bytes = len(manifest_bytes) + len(schema_bytes)
        records: list[OfficialRealEstateTransaction] = []
        for filename, municipality in main_files:
            info = archive.getinfo(filename)
            expanded_bytes += info.file_size
            if expanded_bytes > max_expanded_bytes:
                raise OfficialRealEstateBoundExceeded(
                    "MOI selected CSV files exceed expanded-byte limit"
                )
            records.extend(
                _parse_csv_records(
                    archive.read(filename),
                    headers=MOI_HEADERS,
                    source=artifact.source,
                    municipality=municipality,
                    source_file=filename,
                    max_rows=max_rows - len(records),
                    moi=True,
                )
            )
            if len(records) > max_rows:
                raise OfficialRealEstateBoundExceeded(f"MOI parsed rows exceed {max_rows}")
        schema_sha256 = hashlib.sha256(schema_bytes).hexdigest()
        return records, schema_sha256


def _parse_ntpc_csv(
    artifact: DownloadedArtifact,
    *,
    max_rows: int,
    max_expanded_bytes: int,
) -> tuple[list[OfficialRealEstateTransaction], str]:
    if len(artifact.content) > max_expanded_bytes:
        raise OfficialRealEstateBoundExceeded("NTPC CSV exceeds expanded-byte limit")
    records = _parse_csv_records(
        artifact.content,
        headers=NTPC_HEADERS,
        source=artifact.source,
        municipality=artifact.source.municipality or "",
        source_file="ntpc-real-estate-sales.csv",
        max_rows=max_rows,
        moi=False,
    )
    header_bytes = ",".join(NTPC_HEADERS).encode()
    return records, hashlib.sha256(header_bytes).hexdigest()


def _parse_csv_records(
    payload: bytes,
    *,
    headers: tuple[str, ...],
    source: OfficialSource,
    municipality: str,
    source_file: str,
    max_rows: int,
    moi: bool,
) -> list[OfficialRealEstateTransaction]:
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise OfficialRealEstateSchemaDrift(f"{source_file}: CSV must be UTF-8") from exc
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        actual_headers = tuple(next(reader))
    except StopIteration as exc:
        raise OfficialRealEstateSchemaDrift(f"{source_file}: CSV is empty") from exc
    if actual_headers != headers:
        raise OfficialRealEstateSchemaDrift(
            f"{source_file}: header does not match the approved schema"
        )
    records: list[OfficialRealEstateTransaction] = []
    for row_number, values in enumerate(reader, start=2):
        if not values or all(not value.strip() for value in values):
            continue
        if len(values) != len(headers):
            raise OfficialRealEstateSchemaDrift(
                f"{source_file}:{row_number}: expected {len(headers)} fields, got {len(values)}"
            )
        raw = dict(zip(headers, values, strict=True))
        if moi and _is_moi_english_description_row(raw):
            continue
        if len(records) >= max_rows:
            raise OfficialRealEstateBoundExceeded(
                f"{source_file}: parsed rows exceed configured max_rows"
            )
        records.append(
            _transaction_from_row(
                source=source,
                municipality=municipality,
                source_file=source_file,
                row_number=row_number,
                raw=raw,
                moi=moi,
            )
        )
    return _resolve_source_identities(records)


def _transaction_from_row(
    *,
    source: OfficialSource,
    municipality: str,
    source_file: str,
    row_number: int,
    raw: Mapping[str, str],
    moi: bool,
) -> OfficialRealEstateTransaction:
    def value(moi_name: str, ntpc_name: str) -> str:
        return raw[moi_name if moi else ntpc_name].strip()

    source_record_id = value("編號", "rps27")
    district = value("鄉鎮市區", "district")
    transaction_target = value("交易標的", "rps01")
    if not source_record_id or not district or not municipality or not transaction_target:
        raise OfficialRealEstateSchemaDrift(
            f"{source_file}:{row_number}: required identity field is empty"
        )
    raw_fields = {str(key): str(item) for key, item in raw.items()}
    raw_record_sha256 = hashlib.sha256(
        json.dumps(
            raw_fields,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    completion_date, completion_year, completion_month = _partial_roc_date(
        value("建築完成年月", "rps14_yyymmddroc"),
        field="completion_date",
    )
    return OfficialRealEstateTransaction(
        transaction_id=uuid5(
            _TRANSACTION_NAMESPACE,
            f"{source.source_id}:{source_file}:{source_record_id}",
        ),
        source_id=source.source_id,
        authority_partition=source_file,
        source_record_id=source_record_id,
        source_variant_id="primary",
        identity_fingerprint="",
        municipality=municipality,
        district=district,
        transaction_target=transaction_target,
        address=_clean(value("土地位置建物門牌", "rps02")),
        transaction_date=_roc_date(
            value("交易年月日", "rps07_yyymmddroc"),
            field="transaction_date",
            required=True,
        ),
        transaction_count_summary=_clean(value("交易筆棟數", "rps08")),
        land_area_sqm=_decimal(value("土地移轉總面積平方公尺", "rps03_area")),
        urban_land_use=_clean(value("都市土地使用分區", "rps04")),
        non_urban_land_use=_clean(value("非都市土地使用分區", "rps05")),
        non_urban_land_designation=_clean(value("非都市土地使用編定", "rps06")),
        transferred_floor=_clean(value("移轉層次", "rps09")),
        total_floors=_clean(value("總樓層數", "rps10")),
        building_type=_clean(value("建物型態", "rps11")),
        main_use=_clean(value("主要用途", "rps12")),
        main_material=_clean(value("主要建材", "rps13")),
        completion_date=completion_date,
        completion_year=completion_year,
        completion_month=completion_month,
        building_area_sqm=_decimal(value("建物移轉總面積平方公尺", "rps15_area")),
        room_count=_integer(value("建物現況格局-房", "rps16_quantity")),
        hall_count=_integer(value("建物現況格局-廳", "rps17_quantity")),
        bathroom_count=_integer(value("建物現況格局-衛", "rps18_quantity")),
        has_partition=_yes_no(value("建物現況格局-隔間", "rps19")),
        has_management=_yes_no(value("有無管理組織", "rps20")),
        total_price_twd=_positive_integer(
            value("總價元", "rps21_amountsunitdollars"),
            field="total_price_twd",
        ),
        unit_price_twd_sqm=_integer(value("單價元平方公尺", "rps22_amountsunitdollars")),
        parking_type=_clean(value("車位類別", "rps23")),
        parking_area_sqm=_decimal(value("車位移轉總面積平方公尺", "rps24_area")),
        parking_price_twd=_integer(value("車位總價元", "rps25_amountsunitdollars")),
        remarks=_clean(value("備註", "rps26")),
        main_building_area_sqm=_decimal(value("主建物面積", "rps28_area")),
        auxiliary_building_area_sqm=_decimal(value("附屬建物面積", "rps29_area")),
        balcony_area_sqm=_decimal(value("陽台面積", "rps30_area")),
        has_elevator=_yes_no(value("電梯", "rps31")),
        transfer_number=_clean(value("移轉編號", "rps32")),
        source_file=source_file,
        source_row_number=row_number,
        raw_record_sha256=raw_record_sha256,
        raw_fields=raw_fields,
    )


def _resolve_source_identities(
    records: list[OfficialRealEstateTransaction],
) -> list[OfficialRealEstateTransaction]:
    resolved: list[OfficialRealEstateTransaction] = []
    seen: dict[tuple[str, str, str, str], str] = {}
    for record in records:
        if record.source_id == SOURCES["moi"].source_id:
            variant = "primary"
        else:
            # rps27 is the authority's durable record identity. rps32 is an
            # optional presentation field that may appear on a later snapshot;
            # including it in identity would fork one official sale into two.
            variant = "authority-natural:v1"
        fingerprint = _identity_fingerprint(record)
        key = (
            record.source_id,
            record.authority_partition,
            record.source_record_id,
            variant,
        )
        prior_fingerprint = seen.get(key)
        if prior_fingerprint is not None and prior_fingerprint != fingerprint:
            raise OfficialRealEstateSchemaDrift(
                "authority natural identity collision: the same source record "
                "changed transaction date, address, or transaction target"
            )
        seen[key] = fingerprint
        resolved.append(
            replace(
                record,
                transaction_id=_transaction_id(record, variant),
                source_variant_id=variant,
                identity_fingerprint=fingerprint,
            )
        )
    return resolved


def _transaction_id(
    record: OfficialRealEstateTransaction,
    source_variant_id: str,
) -> UUID:
    return uuid5(
        _TRANSACTION_NAMESPACE,
        (
            f"{record.source_id}:{record.authority_partition}:"
            f"{record.source_record_id}:{source_variant_id}"
        ),
    )


def _identity_fingerprint(record: OfficialRealEstateTransaction) -> str:
    payload = (
        record.transaction_date.isoformat(),
        _identity_text(record.address),
        _identity_text(record.transaction_target),
    )
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def _identity_text(value: str | None) -> str:
    return " ".join(unicodedata.normalize("NFKC", value or "").split())


def _validate_moi_schema(payload: bytes) -> None:
    try:
        rows = list(csv.DictReader(io.StringIO(payload.decode("utf-8-sig"))))
    except UnicodeDecodeError as exc:
        raise OfficialRealEstateSchemaDrift("schema-main.csv must be UTF-8") from exc
    names = tuple(str(row.get("name", "")).strip() for row in rows)
    titles = tuple(str(row.get("title", "")).strip() for row in rows)
    if names != MOI_HEADERS or titles != MOI_HEADERS:
        raise OfficialRealEstateSchemaDrift(
            "schema-main.csv does not match the approved MOI main schema"
        )


def _moi_main_files(
    payload: bytes,
    member_names: set[str],
) -> tuple[tuple[str, str], ...]:
    try:
        rows = list(csv.DictReader(io.StringIO(payload.decode("utf-8-sig"))))
    except UnicodeDecodeError as exc:
        raise OfficialRealEstateSchemaDrift("manifest.csv must be UTF-8") from exc
    files: list[tuple[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        filename = str(row.get("name", "")).strip()
        if not _MOI_MAIN_FILE.fullmatch(filename):
            continue
        if filename in seen or filename not in member_names:
            raise OfficialRealEstateSchemaDrift(
                f"manifest.csv has invalid main-file registration for {filename}"
            )
        if str(row.get("schema", "")).strip() != "schema-main.csv":
            raise OfficialRealEstateSchemaDrift(
                f"manifest.csv binds {filename} to an unexpected schema"
            )
        description = str(row.get("description", "")).strip()
        suffix = "不動產買賣"
        if not description.endswith(suffix):
            raise OfficialRealEstateSchemaDrift(
                f"manifest.csv has an invalid municipality description for {filename}"
            )
        municipality = description[: -len(suffix)]
        if not municipality:
            raise OfficialRealEstateSchemaDrift(f"manifest.csv has no municipality for {filename}")
        seen.add(filename)
        files.append((filename, municipality))
    if not files:
        raise OfficialRealEstateSchemaDrift("manifest.csv contains no registered sale main files")
    return tuple(sorted(files))


def _is_moi_english_description_row(raw: Mapping[str, str]) -> bool:
    return (
        raw.get("鄉鎮市區", "").startswith("The villages and towns")
        and raw.get("編號") == "serial number"
        and raw.get("總價元") == "total price NTD"
    )


def _roc_date(value: str, *, field: str, required: bool) -> date | None:
    normalized = value.strip()
    if not normalized or set(normalized) == {"0"}:
        if required:
            raise OfficialRealEstateSchemaDrift(f"{field} is required")
        return None
    if len(normalized) != 7 or not normalized.isdigit():
        raise OfficialRealEstateSchemaDrift(f"{field} must be a seven-digit ROC calendar date")
    year = int(normalized[:3]) + 1911
    try:
        return date(year, int(normalized[3:5]), int(normalized[5:7]))
    except ValueError as exc:
        raise OfficialRealEstateSchemaDrift(f"{field} is not a valid date") from exc


def _partial_roc_date(
    value: str,
    *,
    field: str,
) -> tuple[date | None, int | None, int | None]:
    normalized = value.strip()
    if not normalized or set(normalized) == {"0"}:
        return None, None, None
    if not normalized.isdigit() or len(normalized) not in {3, 5, 7}:
        raise OfficialRealEstateSchemaDrift(
            f"{field} must contain a ROC year, year-month, or full date"
        )
    year = int(normalized[:3]) + 1911
    if len(normalized) == 3:
        return None, year, None
    month = int(normalized[3:5])
    if month == 0:
        if len(normalized) == 7 and int(normalized[5:7]) != 0:
            raise OfficialRealEstateSchemaDrift(f"{field} has a day without a month")
        return None, year, None
    if month > 12:
        raise OfficialRealEstateSchemaDrift(f"{field} month is invalid")
    if len(normalized) == 5:
        return None, year, month
    day = int(normalized[5:7])
    if day == 0:
        return None, year, month
    try:
        return date(year, month, day), year, month
    except ValueError:
        # The official source can contain impossible legacy days (for example
        # June 31). Preserve its real year/month and raw field without inventing
        # a replacement day.
        return None, year, month


def _decimal(value: str) -> Decimal | None:
    normalized = value.strip()
    if not normalized:
        return None
    try:
        parsed = Decimal(normalized)
    except InvalidOperation as exc:
        raise OfficialRealEstateSchemaDrift(f"invalid decimal value {normalized!r}") from exc
    if not parsed.is_finite() or parsed < 0:
        raise OfficialRealEstateSchemaDrift(
            f"decimal value must be finite and non-negative: {normalized!r}"
        )
    return parsed


def _integer(value: str) -> int | None:
    normalized = value.strip()
    if not normalized:
        return None
    try:
        parsed = int(normalized)
    except ValueError as exc:
        raise OfficialRealEstateSchemaDrift(f"invalid integer value {normalized!r}") from exc
    if parsed < 0:
        raise OfficialRealEstateSchemaDrift(f"integer value must be non-negative: {normalized!r}")
    return parsed


def _positive_integer(value: str, *, field: str) -> int:
    parsed = _integer(value)
    if parsed is None or parsed <= 0:
        raise OfficialRealEstateSchemaDrift(f"{field} must be greater than zero")
    return parsed


def _yes_no(value: str) -> bool | None:
    normalized = value.strip()
    if not normalized:
        return None
    if normalized == "有":
        return True
    if normalized == "無":
        return False
    raise OfficialRealEstateSchemaDrift(
        f"boolean field contains an unsupported value: {normalized!r}"
    )


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _require_approved_source(source: OfficialSource) -> None:
    approved = SOURCES.get(source.key)
    if approved is None or source != approved:
        raise OfficialRealEstateSourceError(
            f"{source.key}: source metadata does not match the approved registry"
        )


def _optional_positive_int(value: Any) -> int | None:
    normalized = _clean(value)
    if normalized is None:
        return None
    try:
        parsed = int(normalized)
    except ValueError as exc:
        raise OfficialRealEstateSourceError("Content-Length is not an integer") from exc
    if parsed < 0:
        raise OfficialRealEstateSourceError("Content-Length cannot be negative")
    return parsed


__all__ = [
    "GOVERNMENT_OPEN_DATA_LICENSE_V1",
    "MAX_DOWNLOAD_BYTES",
    "MAX_EXPANDED_BYTES",
    "MAX_ROWS",
    "MOI_HEADERS",
    "MOI_SOURCE_URL",
    "NTPC_HEADERS",
    "NTPC_SOURCE_URL",
    "PARSER_VERSION",
    "SOURCES",
    "DownloadedArtifact",
    "OfficialRealEstateBoundExceeded",
    "OfficialRealEstateSchemaDrift",
    "OfficialRealEstateSourceError",
    "OfficialRealEstateTransaction",
    "OfficialSource",
    "ParsedOfficialRealEstateBatch",
    "parse_official_real_estate",
]

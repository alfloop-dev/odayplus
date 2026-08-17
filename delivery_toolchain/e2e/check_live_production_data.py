#!/usr/bin/env python3
"""Prove that a deployed ODay Plus runtime is backed by live production data.

This gate is deliberately independent from application dependency injection.
It verifies three separately supplied sources of proof:

* the deployed API readiness/version surfaces and per-service receipt surfaces;
* PostgreSQL ingestion, raw, quarantine, canonical, and lineage records;
* a commit-bound evidence manifest that must agree with both runtime and SQL.

Missing inputs, empty datasets, stale evidence, reconciliation differences,
surrogate persistence, fixture markers, or incomplete model lineage all fail
closed. Secret values are consumed from named environment variables and are
never written to the report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = (
    ROOT / ".odp_data" / "live-production-data-gate" / "live-production-data-report.json"
)

API_URL_ENV = "ODP_LIVE_API_URL"
POSTGRES_DSN_ENV = "ODP_LIVE_POSTGRES_DSN"
EVIDENCE_PATH_ENV = "ODP_LIVE_DATA_EVIDENCE"
EXPECTED_SHA_ENV = "ODP_LIVE_EXPECTED_SHA"
BEARER_TOKEN_ENV = "ODP_LIVE_BEARER_TOKEN"

REQUIRED_DATASETS = ("merchant", "place", "transaction")
REQUIRED_SERVICES = (
    "operator",
    "forecastops",
    "sitescore",
    "avm",
    "netplan",
    "priceops",
    "adlift",
    "learninghub",
)
MODEL_SERVICES = frozenset(REQUIRED_SERVICES) - {"operator"}
SUCCESS_STATUSES = frozenset({"SUCCEEDED", "COMPLETED", "READY"})
POSTGRES_MODES = frozenset({"postgres", "postgresql"})
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
DIGEST_PATTERN = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$", re.IGNORECASE)
SQL_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")
SURROGATE_TOKEN = re.compile(
    r"(?:^|[/_.:@-])(mock|fixture|synthetic|seed(?:ed)?|demo|sample|fake)"
    r"(?:$|[/_.:@-])",
    re.IGNORECASE,
)
SURROGATE_EXACT = frozenset(
    {
        "fixture",
        "mock",
        "synthetic",
        "seed",
        "seeded",
        "demo",
        "sample",
        "fake",
        "memory",
        "in-memory",
        "sqlite",
        "local-baseline-seed",
        "r4-seed",
    }
)
FORBIDDEN_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "example.com"})
RAW_TABLES = {
    "merchant": "raw_merchant",
    "place": "raw_place",
    "transaction": "raw_transaction",
}


@dataclass(frozen=True)
class CheckResult:
    ok: bool
    name: str
    detail: str


@dataclass(frozen=True)
class GateConfig:
    api_url: str
    postgres_dsn: str
    evidence_path: Path
    expected_sha: str
    expected_deployment: str = "production"
    freshness_hours: float = 30.0
    timeout: float = 15.0
    bearer_token: str = ""
    control_schema: str = "data_plane"
    raw_schema: str = "fongniao_raw"
    allow_http: bool = False


class HttpEvidenceClient(Protocol):
    def get_json(self, path: str) -> dict[str, Any]: ...


class DatabaseEvidenceClient(Protocol):
    def collect(self) -> dict[str, Any]: ...


class UrllibHttpEvidenceClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout: float,
        bearer_token: str = "",
        correlation_id: str,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._bearer_token = bearer_token
        self._correlation_id = correlation_id

    def get_json(self, path: str) -> dict[str, Any]:
        safe_path = _safe_relative_api_path(path)
        headers = {
            "accept": "application/json",
            "x-correlation-id": self._correlation_id,
        }
        if self._bearer_token:
            headers["authorization"] = f"Bearer {self._bearer_token}"
        request = urllib.request.Request(f"{self._base_url}{safe_path}", headers=headers)
        try:
            with urllib.request.urlopen(  # noqa: S310 - validated operator URL
                request, timeout=self._timeout
            ) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"HTTP {exc.code} for {safe_path}") from exc
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"request failed for {safe_path}: {type(exc).__name__}") from exc
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"non-JSON response for {safe_path}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"JSON object required for {safe_path}")
        return payload


class PsycopgDatabaseEvidenceClient:
    """Read-only collector for the production data-plane reconciliation proof."""

    def __init__(
        self,
        dsn: str,
        *,
        control_schema: str,
        raw_schema: str,
        connect: Any | None = None,
    ) -> None:
        self._dsn = dsn
        self._control_schema = _safe_sql_identifier(control_schema, "control schema")
        self._raw_schema = _safe_sql_identifier(raw_schema, "raw schema")
        if connect is None:
            try:
                import psycopg
            except ImportError as exc:  # pragma: no cover - deployment dependency
                raise RuntimeError("psycopg is required for PostgreSQL verification") from exc
            connect = psycopg.connect
        self._connect = connect

    def collect(self) -> dict[str, Any]:
        datasets: dict[str, dict[str, Any]] = {}
        with self._connect(self._dsn) as connection:
            connection.execute("SET TRANSACTION READ ONLY")
            identity = connection.execute(
                """
                SELECT current_database(), current_setting('server_version'),
                       current_setting('transaction_read_only')
                """
            ).fetchone()
            if identity is None:
                raise RuntimeError("PostgreSQL identity query returned no row")

            for source_kind in REQUIRED_DATASETS:
                run = connection.execute(
                    f"""
                    SELECT run_id::text, source_database, source_kind, partition_key,
                           status, processed_count, valid_loaded, quarantined_count,
                           source_checksum, raw_checksum, canonical_checksum,
                           started_at, finished_at
                    FROM {self._control_schema}.ingestion_runs
                    WHERE source_kind = %s
                    ORDER BY finished_at DESC NULLS LAST, started_at DESC
                    LIMIT 1
                    """,
                    (source_kind,),
                ).fetchone()
                if run is None:
                    datasets[source_kind] = {"source_kind": source_kind, "missing": True}
                    continue

                run_id = str(run[0])
                raw_table = RAW_TABLES[source_kind]
                raw = connection.execute(
                    f"""
                    SELECT COUNT(*), MAX(observed_at),
                           COUNT(*) FILTER (
                               WHERE lower(source_id::text) ~
                                   '(^|[/_.:@-])(mock|fixture|synthetic|seed|demo|sample|fake)([/_.:@-]|$)'
                                  OR lower(source_document::text) ~
                                   '"(data_origin|provider|mode|source)"[ ]*:[ ]*"(mock|fixture|synthetic|seed|memory)'
                           )
                    FROM {self._raw_schema}.{raw_table}
                    WHERE run_id = %s
                    """,
                    (run_id,),
                ).fetchone()
                raw_count, raw_computed_checksum = self._query_checksum(
                    connection.execute(
                        f"""
                        SELECT source_snapshot_id::text, content_sha256
                        FROM {self._raw_schema}.{raw_table}
                        WHERE run_id = %s
                        ORDER BY source_snapshot_id::text, content_sha256
                        """,
                        (run_id,),
                    )
                )
                valid_count, valid_computed_checksum = self._query_checksum(
                    connection.execute(
                        f"""
                        SELECT DISTINCT raw.source_snapshot_id::text, raw.content_sha256
                        FROM {self._raw_schema}.{raw_table} AS raw
                        JOIN {self._control_schema}.canonical_lineage AS lineage
                          ON lineage.run_id = raw.run_id
                         AND lineage.source_kind = %s
                         AND lineage.source_snapshot_id = raw.source_snapshot_id
                         AND lineage.content_sha256 = raw.content_sha256
                        WHERE raw.run_id = %s
                        ORDER BY raw.source_snapshot_id::text, raw.content_sha256
                        """,
                        (source_kind, run_id),
                    )
                )
                lineage = connection.execute(
                    f"""
                    SELECT COUNT(DISTINCT source_snapshot_id), MAX(projected_at)
                    FROM {self._control_schema}.canonical_lineage
                    WHERE run_id = %s AND source_kind = %s
                    """,
                    (run_id, source_kind),
                ).fetchone()
                canonical_count, canonical_computed_checksum = self._query_checksum(
                    connection.execute(
                        f"""
                        SELECT DISTINCT source_snapshot_id::text, content_sha256
                        FROM {self._control_schema}.canonical_lineage
                        WHERE run_id = %s AND source_kind = %s
                        ORDER BY source_snapshot_id::text, content_sha256
                        """,
                        (run_id, source_kind),
                    )
                )
                quarantine = connection.execute(
                    f"""
                    SELECT COUNT(*)
                    FROM {self._control_schema}.quarantined_records
                    WHERE run_id = %s AND source_kind = %s AND resolved_at IS NULL
                    """,
                    (run_id, source_kind),
                ).fetchone()
                datasets[source_kind] = {
                    "source_kind": source_kind,
                    "run_id": run_id,
                    "source_database": str(run[1]),
                    "partition_key": str(run[3]),
                    "status": str(run[4]),
                    "source_count": int(run[5]),
                    "valid_loaded": int(run[6]),
                    "quarantined_count": int(run[7]),
                    "source_checksum": str(run[8] or ""),
                    "raw_checksum": str(run[9] or ""),
                    "canonical_checksum": str(run[10] or ""),
                    "started_at": _iso(run[11]),
                    "finished_at": _iso(run[12]),
                    "raw_count": raw_count,
                    "raw_computed_checksum": raw_computed_checksum,
                    "valid_computed_checksum": valid_computed_checksum,
                    "latest_observed_at": _iso(raw[1] if raw else None),
                    "surrogate_marker_count": int(raw[2] if raw else 0),
                    "canonical_count": canonical_count,
                    "canonical_computed_checksum": canonical_computed_checksum,
                    "lineage_count": int(lineage[0] if lineage else 0),
                    "latest_projected_at": _iso(lineage[1] if lineage else None),
                    "active_quarantine_count": int(quarantine[0] if quarantine else 0),
                }

        return {
            "database": {
                "name": str(identity[0]),
                "server_version": str(identity[1]),
                "transaction_read_only": str(identity[2]),
            },
            "datasets": datasets,
        }

    @staticmethod
    def _query_checksum(rows: Any) -> tuple[int, str]:
        digest = hashlib.sha256()
        count = 0
        for row in rows:
            if count:
                digest.update(b"\n")
            digest.update(f"{row[0]}:{row[1]}".encode())
            count += 1
        return count, digest.hexdigest()


def _iso(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _safe_sql_identifier(value: str, label: str) -> str:
    normalized = value.strip()
    if not SQL_IDENTIFIER.fullmatch(normalized):
        raise ValueError(f"{label} must be a lowercase SQL identifier")
    return normalized


def _safe_relative_api_path(path: str) -> str:
    value = str(path or "").strip()
    parsed = urllib.parse.urlsplit(value)
    if (
        not value.startswith("/")
        or value.startswith("//")
        or parsed.scheme
        or parsed.netloc
        or parsed.query
        or parsed.fragment
        or ".." in parsed.path.split("/")
    ):
        raise ValueError("receipt proof path must be an absolute-path reference without query data")
    return parsed.path


def _normalize_api_url(value: str, *, allow_http: bool) -> str:
    parsed = urllib.parse.urlsplit(value.strip())
    allowed_schemes = {"https"}
    if allow_http:
        allowed_schemes.add("http")
    if (
        parsed.scheme not in allowed_schemes
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("API URL must be a credential-free HTTPS origin")
    if not allow_http and parsed.hostname.lower() in FORBIDDEN_HOSTS:
        raise ValueError("API URL must identify a deployed non-example host")
    path = parsed.path.rstrip("/")
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, path, "", "")
    ).rstrip("/")


def _validate_postgres_dsn(dsn: str) -> None:
    parsed = urllib.parse.urlsplit(dsn.strip())
    if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname:
        raise ValueError("live PostgreSQL DSN must use postgres:// or postgresql://")
    if _contains_surrogate_marker(parsed.hostname):
        raise ValueError("live PostgreSQL DSN host contains a surrogate marker")


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _contains_surrogate_marker(value: str) -> bool:
    normalized = value.strip().lower()
    return normalized in SURROGATE_EXACT or bool(SURROGATE_TOKEN.search(normalized))


def find_surrogate_values(value: Any, path: str = "$") -> list[str]:
    findings: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            findings.extend(find_surrogate_values(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            findings.extend(find_surrogate_values(child, f"{path}[{index}]"))
    elif isinstance(value, str):
        parsed = urllib.parse.urlsplit(value)
        bad_host = parsed.hostname and parsed.hostname.lower() in FORBIDDEN_HOSTS
        if _contains_surrogate_marker(value) or bad_host:
            findings.append(path)
    return findings


def _check(
    checks: list[CheckResult],
    ok: bool,
    name: str,
    detail: str,
) -> None:
    checks.append(CheckResult(bool(ok), name, detail))


def _is_fresh(value: Any, *, now: datetime, freshness: timedelta) -> bool:
    parsed = _parse_time(value)
    return parsed is not None and now - freshness <= parsed <= now + timedelta(minutes=5)


def _redactor(*secret_values: str):
    secrets = sorted(
        {value for value in secret_values if value},
        key=len,
        reverse=True,
    )

    def redact(value: Any) -> str:
        text = str(value)
        for secret in secrets:
            text = text.replace(secret, "<redacted>")
        text = re.sub(
            r"\bpostgres(?:ql)?://[^\s]+",
            "<redacted-postgres-dsn>",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(
            r"\bBearer\s+[A-Za-z0-9._~+/=-]+",
            "Bearer <redacted>",
            text,
            flags=re.IGNORECASE,
        )
        return text

    return redact


def validate_config(config: GateConfig) -> list[CheckResult]:
    checks: list[CheckResult] = []
    try:
        _normalize_api_url(config.api_url, allow_http=config.allow_http)
        _check(checks, True, "config:api_url", "configured deployed API origin")
    except ValueError as exc:
        _check(checks, False, "config:api_url", str(exc))

    try:
        _validate_postgres_dsn(config.postgres_dsn)
        _check(checks, True, "config:postgres_dsn", "configured PostgreSQL DSN (redacted)")
    except ValueError as exc:
        _check(checks, False, "config:postgres_dsn", str(exc))

    _check(
        checks,
        config.evidence_path.is_file(),
        "config:evidence",
        "configured evidence file" if config.evidence_path.is_file() else "missing evidence file",
    )
    _check(
        checks,
        bool(SHA_PATTERN.fullmatch(config.expected_sha)),
        "config:expected_sha",
        config.expected_sha if SHA_PATTERN.fullmatch(config.expected_sha) else "missing/invalid SHA",
    )
    _check(
        checks,
        config.expected_deployment in {"production", "staging", "dev"},
        "config:expected_deployment",
        config.expected_deployment or "missing",
    )
    _check(
        checks,
        config.freshness_hours > 0,
        "config:freshness_hours",
        str(config.freshness_hours),
    )
    for value, label in (
        (config.control_schema, "control schema"),
        (config.raw_schema, "raw schema"),
    ):
        try:
            _safe_sql_identifier(value, label)
            _check(checks, True, f"config:{label.replace(' ', '_')}", value)
        except ValueError as exc:
            _check(checks, False, f"config:{label.replace(' ', '_')}", str(exc))
    return checks


def _validate_readiness(
    payload: dict[str, Any],
    *,
    expected_deployment: str,
    checks: list[CheckResult],
) -> None:
    details = payload.get("details")
    details = details if isinstance(details, dict) else {}
    persistence = details.get("persistence")
    persistence = persistence if isinstance(persistence, dict) else {}
    provider = details.get("provider")
    provider = provider if isinstance(provider, dict) else {}
    models = details.get("models")
    models = models if isinstance(models, dict) else {}
    data = details.get("data")
    data = data if isinstance(data, dict) else {}
    probe = data.get("operatorRepositoryProbe")
    probe = probe if isinstance(probe, dict) else {}

    _check(checks, payload.get("status") == "ok", "api:readiness:status", str(payload.get("status")))
    _check(
        checks,
        details.get("requireLiveData") is True,
        "api:readiness:require_live_data",
        str(details.get("requireLiveData")),
    )
    _check(
        checks,
        details.get("deploymentMode") == expected_deployment,
        "api:readiness:deployment",
        str(details.get("deploymentMode")),
    )
    _check(
        checks,
        str(persistence.get("configuredMode")).lower() in POSTGRES_MODES
        and str(persistence.get("runtimeMode")).lower() in POSTGRES_MODES
        and persistence.get("durable") is True
        and persistence.get("reachable") is True
        and persistence.get("production_persistence_supported") is True,
        "api:readiness:persistence",
        (
            f"configured={persistence.get('configuredMode')} "
            f"runtime={persistence.get('runtimeMode')} "
            f"durable={persistence.get('durable')} "
            f"reachable={persistence.get('reachable')}"
        ),
    )
    _check(
        checks,
        provider.get("mode") == "live"
        and provider.get("configurationValid") is True
        and provider.get("healthy") is True
        and provider.get("live") is True,
        "api:readiness:provider",
        f"mode={provider.get('mode')} live={provider.get('live')}",
    )
    _check(
        checks,
        models.get("mode") == "mlflow-production"
        and models.get("productionBindingsReady") is True
        and models.get("autoSeeded") is False
        and not models.get("error"),
        "api:readiness:model_bindings",
        (
            f"mode={models.get('mode')} "
            f"ready={models.get('productionBindingsReady')} "
            f"autoSeeded={models.get('autoSeeded')}"
        ),
    )
    origin = data.get("origin")
    origin = origin if isinstance(origin, dict) else {}
    _check(
        checks,
        data.get("mode") == "live"
        and data.get("liveReady") is True
        and data.get("operatorRepositoryReady") is True
        and origin.get("kind") == "live"
        and str(origin.get("persistenceMode")).lower() in POSTGRES_MODES
        and probe.get("ready") is True
        and str(probe.get("persistenceMode")).lower() in POSTGRES_MODES,
        "api:readiness:data_origin",
        (
            f"mode={data.get('mode')} origin={origin.get('kind')} "
            f"operatorReady={data.get('operatorRepositoryReady')}"
        ),
    )
    blockers = data.get("blockingReasons", [])
    _check(
        checks,
        isinstance(blockers, list) and not blockers,
        "api:readiness:blockers",
        f"count={len(blockers) if isinstance(blockers, list) else 'invalid'}",
    )
    marker_paths = find_surrogate_values(payload)
    _check(
        checks,
        not marker_paths,
        "api:readiness:no_surrogate_markers",
        "none" if not marker_paths else f"paths={marker_paths[:10]}",
    )


def _dataset_map(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    datasets = payload.get("datasets", {})
    if isinstance(datasets, dict):
        return {
            str(key): value
            for key, value in datasets.items()
            if isinstance(value, dict)
        }
    if isinstance(datasets, list):
        result: dict[str, dict[str, Any]] = {}
        for item in datasets:
            if isinstance(item, dict) and item.get("source_kind"):
                result[str(item["source_kind"])] = item
        return result
    return {}


def _validate_database(
    database_payload: dict[str, Any],
    *,
    evidence: dict[str, Any],
    now: datetime,
    freshness: timedelta,
    checks: list[CheckResult],
) -> None:
    identity = database_payload.get("database")
    identity = identity if isinstance(identity, dict) else {}
    _check(
        checks,
        bool(identity.get("name"))
        and bool(identity.get("server_version"))
        and identity.get("transaction_read_only") == "on",
        "database:identity",
        (
            f"name={identity.get('name') or 'missing'} "
            f"version={identity.get('server_version') or 'missing'} "
            f"readOnly={identity.get('transaction_read_only') or 'missing'}"
        ),
    )
    db_datasets = _dataset_map(database_payload)
    evidence_datasets = _dataset_map(evidence)

    for source_kind in REQUIRED_DATASETS:
        actual = db_datasets.get(source_kind, {})
        declared = evidence_datasets.get(source_kind, {})
        prefix = f"database:{source_kind}"
        _check(
            checks,
            bool(actual) and not actual.get("missing"),
            f"{prefix}:run_exists",
            str(actual.get("run_id") or "missing"),
        )
        source_count = int(actual.get("source_count") or 0)
        raw_count = int(actual.get("raw_count") or 0)
        valid_loaded = int(actual.get("valid_loaded") or 0)
        canonical_count = int(actual.get("canonical_count") or 0)
        quarantine_count = int(actual.get("quarantined_count") or 0)
        active_quarantine = int(actual.get("active_quarantine_count") or 0)
        _check(
            checks,
            actual.get("status") == "SUCCEEDED",
            f"{prefix}:status",
            str(actual.get("status") or "missing"),
        )
        _check(
            checks,
            source_count > 0 and raw_count > 0 and valid_loaded > 0 and canonical_count > 0,
            f"{prefix}:populated",
            (
                f"source={source_count} raw={raw_count} "
                f"valid={valid_loaded} canonical={canonical_count}"
            ),
        )
        arithmetic_ok = (
            source_count == raw_count
            and source_count == valid_loaded + quarantine_count
            and valid_loaded == canonical_count
            and quarantine_count == active_quarantine
            and int(actual.get("lineage_count") or 0) == canonical_count
        )
        _check(
            checks,
            arithmetic_ok,
            f"{prefix}:reconciliation_arithmetic",
            (
                f"source={source_count} raw={raw_count} valid={valid_loaded} "
                f"canonical={canonical_count} quarantine={quarantine_count} "
                f"activeQuarantine={active_quarantine}"
            ),
        )
        checksums_ok = (
            bool(actual.get("source_checksum"))
            and actual.get("source_checksum") == actual.get("raw_checksum")
            and actual.get("raw_checksum") == actual.get("raw_computed_checksum")
            and actual.get("valid_computed_checksum")
            == actual.get("canonical_computed_checksum")
            and bool(actual.get("canonical_checksum"))
            and actual.get("canonical_checksum")
            == actual.get("canonical_computed_checksum")
        )
        _check(
            checks,
            checksums_ok,
            f"{prefix}:checksums",
            (
                f"sourceRawMatch={actual.get('source_checksum') == actual.get('raw_checksum')} "
                f"rawComputedMatch={actual.get('raw_checksum') == actual.get('raw_computed_checksum')} "
                "validCanonicalMatch="
                f"{actual.get('valid_computed_checksum') == actual.get('canonical_computed_checksum')} "
                "canonicalStoredMatch="
                f"{actual.get('canonical_checksum') == actual.get('canonical_computed_checksum')}"
            ),
        )
        timestamps = (
            actual.get("finished_at"),
            actual.get("latest_observed_at"),
            actual.get("latest_projected_at"),
        )
        _check(
            checks,
            all(_is_fresh(value, now=now, freshness=freshness) for value in timestamps),
            f"{prefix}:freshness",
            "finished/observed/projected timestamps checked",
        )
        _check(
            checks,
            actual.get("source_database") == "fongniao_prod",
            f"{prefix}:source_database",
            str(actual.get("source_database") or "missing"),
        )
        _check(
            checks,
            int(actual.get("surrogate_marker_count") or 0) == 0,
            f"{prefix}:no_surrogate_rows",
            f"count={int(actual.get('surrogate_marker_count') or 0)}",
        )
        comparable_fields = (
            "run_id",
            "source_count",
            "raw_count",
            "valid_loaded",
            "canonical_count",
            "quarantined_count",
            "source_checksum",
            "raw_checksum",
            "valid_checksum",
            "canonical_checksum",
        )
        mismatches = [
            field
            for field in comparable_fields
            if declared.get(field)
            != (
                actual.get("valid_computed_checksum")
                if field == "valid_checksum"
                else actual.get(field)
            )
        ]
        _check(
            checks,
            bool(declared) and not mismatches,
            f"{prefix}:evidence_agrees",
            "matched" if declared and not mismatches else f"mismatches={mismatches}",
        )


def _find_receipt(payload: Any, receipt_id: str) -> dict[str, Any] | None:
    if isinstance(payload, dict):
        if str(payload.get("receipt_id") or "") == receipt_id:
            return payload
        for child in payload.values():
            found = _find_receipt(child, receipt_id)
            if found is not None:
                return found
    elif isinstance(payload, list):
        for child in payload:
            found = _find_receipt(child, receipt_id)
            if found is not None:
                return found
    return None


def _validate_receipts(
    evidence: dict[str, Any],
    *,
    http_client: HttpEvidenceClient,
    now: datetime,
    freshness: timedelta,
    checks: list[CheckResult],
) -> None:
    receipts = evidence.get("runtime_receipts")
    receipts = receipts if isinstance(receipts, dict) else {}
    for service in REQUIRED_SERVICES:
        receipt = receipts.get(service)
        receipt = receipt if isinstance(receipt, dict) else {}
        prefix = f"receipt:{service}"
        receipt_id = str(receipt.get("receipt_id") or "")
        run_id = str(receipt.get("run_id") or "")
        _check(
            checks,
            bool(receipt_id) and bool(run_id),
            f"{prefix}:identity",
            f"receiptId={'present' if receipt_id else 'missing'} runId={'present' if run_id else 'missing'}",
        )
        _check(
            checks,
            str(receipt.get("status") or "").upper() in SUCCESS_STATUSES,
            f"{prefix}:status",
            str(receipt.get("status") or "missing"),
        )
        _check(
            checks,
            _is_fresh(receipt.get("occurred_at"), now=now, freshness=freshness),
            f"{prefix}:freshness",
            "occurred_at checked",
        )
        source_snapshot_ids = receipt.get("source_snapshot_ids")
        _check(
            checks,
            receipt.get("data_origin") == "live"
            and str(receipt.get("persistence") or "").lower() in POSTGRES_MODES
            and isinstance(source_snapshot_ids, list)
            and bool(source_snapshot_ids),
            f"{prefix}:live_lineage",
            (
                f"origin={receipt.get('data_origin')} "
                f"persistence={receipt.get('persistence')} "
                f"snapshots={len(source_snapshot_ids) if isinstance(source_snapshot_ids, list) else 0}"
            ),
        )
        marker_paths = find_surrogate_values(receipt)
        _check(
            checks,
            not marker_paths,
            f"{prefix}:no_surrogate_markers",
            "none" if not marker_paths else f"paths={marker_paths[:10]}",
        )

        if service in MODEL_SERVICES:
            binding = receipt.get("model_binding")
            binding = binding if isinstance(binding, dict) else {}
            required_binding = (
                "model_name",
                "model_version",
                "artifact_uri",
                "artifact_sha256",
                "registry_run_id",
                "feature_schema_version",
                "dataset_snapshot_id",
            )
            missing_binding = [field for field in required_binding if not binding.get(field)]
            artifact_uri = str(binding.get("artifact_uri") or "")
            artifact_scheme = urllib.parse.urlsplit(artifact_uri).scheme.lower()
            digest = str(binding.get("artifact_sha256") or "")
            _check(
                checks,
                not missing_binding
                and artifact_scheme in {"https", "gs", "s3", "mlflow-artifacts"}
                and bool(DIGEST_PATTERN.fullmatch(digest)),
                f"{prefix}:model_binding",
                (
                    f"missing={missing_binding} artifactScheme={artifact_scheme or 'missing'} "
                    f"digestValid={bool(DIGEST_PATTERN.fullmatch(digest))}"
                ),
            )
            execution = receipt.get("execution")
            execution = execution if isinstance(execution, dict) else {}
            _check(
                checks,
                bool(execution.get("engine"))
                and bool(execution.get("library"))
                and execution.get("actual_model_invoked") is True
                and execution.get("fallback_used") is False,
                f"{prefix}:model_execution",
                (
                    f"engine={execution.get('engine') or 'missing'} "
                    f"library={execution.get('library') or 'missing'} "
                    f"invoked={execution.get('actual_model_invoked')} "
                    f"fallback={execution.get('fallback_used')}"
                ),
            )

        proof_path = str(receipt.get("proof_path") or "")
        try:
            safe_path = _safe_relative_api_path(proof_path)
            runtime_payload = http_client.get_json(safe_path)
            runtime_receipt = _find_receipt(runtime_payload, receipt_id)
            proof_ok = (
                runtime_receipt is not None
                and str(runtime_receipt.get("run_id") or "") == run_id
                and str(runtime_receipt.get("status") or "").upper()
                == str(receipt.get("status") or "").upper()
                and not find_surrogate_values(runtime_payload)
            )
            _check(
                checks,
                proof_ok,
                f"{prefix}:runtime_proof",
                f"path={safe_path} matched={proof_ok}",
            )
        except (RuntimeError, ValueError) as exc:
            _check(checks, False, f"{prefix}:runtime_proof", str(exc))


def evaluate_gate(
    config: GateConfig,
    *,
    http_client: HttpEvidenceClient,
    database_client: DatabaseEvidenceClient,
    evidence_payload: dict[str, Any],
    now: datetime | None = None,
) -> tuple[list[CheckResult], dict[str, Any]]:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    freshness = timedelta(hours=config.freshness_hours)
    checks = validate_config(config)
    redact = _redactor(config.postgres_dsn, config.bearer_token)
    normalized_api = ""
    try:
        normalized_api = _normalize_api_url(config.api_url, allow_http=config.allow_http)
    except ValueError:
        pass

    evidence_markers = find_surrogate_values(evidence_payload)
    _check(
        checks,
        not evidence_markers,
        "evidence:no_surrogate_markers",
        "none" if not evidence_markers else f"paths={evidence_markers[:20]}",
    )
    _check(
        checks,
        evidence_payload.get("schema_version") == 1,
        "evidence:schema_version",
        str(evidence_payload.get("schema_version") or "missing"),
    )
    _check(
        checks,
        evidence_payload.get("release_sha") == config.expected_sha,
        "evidence:release_sha",
        str(evidence_payload.get("release_sha") or "missing"),
    )
    evidence_api = str(evidence_payload.get("api_url") or "")
    try:
        evidence_api = _normalize_api_url(evidence_api, allow_http=config.allow_http)
    except ValueError:
        evidence_api = ""
    _check(
        checks,
        bool(normalized_api) and evidence_api == normalized_api,
        "evidence:api_binding",
        "matched" if evidence_api == normalized_api and evidence_api else "mismatch",
    )
    _check(
        checks,
        evidence_payload.get("data_mode") == "live"
        and str(evidence_payload.get("persistence") or "").lower() in POSTGRES_MODES
        and evidence_payload.get("source_database") == "fongniao_prod",
        "evidence:live_data_contract",
        (
            f"mode={evidence_payload.get('data_mode')} "
            f"persistence={evidence_payload.get('persistence')} "
            f"source={evidence_payload.get('source_database')}"
        ),
    )
    _check(
        checks,
        _is_fresh(evidence_payload.get("generated_at"), now=now, freshness=freshness),
        "evidence:freshness",
        "generated_at checked",
    )

    try:
        readiness = http_client.get_json("/readiness")
        _validate_readiness(
            readiness,
            expected_deployment=config.expected_deployment,
            checks=checks,
        )
    except (RuntimeError, ValueError) as exc:
        _check(checks, False, "api:readiness", redact(exc))

    try:
        version = http_client.get_json("/platform/version")
        actual_sha = str(version.get("release_sha") or "")
        _check(
            checks,
            actual_sha == config.expected_sha,
            "api:release_sha",
            actual_sha or "missing",
        )
        marker_paths = find_surrogate_values(version)
        _check(
            checks,
            not marker_paths,
            "api:version:no_surrogate_markers",
            "none" if not marker_paths else f"paths={marker_paths[:10]}",
        )
    except (RuntimeError, ValueError) as exc:
        _check(checks, False, "api:release_sha", redact(exc))

    try:
        database_payload = database_client.collect()
        _validate_database(
            database_payload,
            evidence=evidence_payload,
            now=now,
            freshness=freshness,
            checks=checks,
        )
    except Exception as exc:  # noqa: BLE001 - any SQL uncertainty blocks production
        _check(
            checks,
            False,
            "database:collect",
            redact(f"{type(exc).__name__}: {exc}"),
        )

    _validate_receipts(
        evidence_payload,
        http_client=http_client,
        now=now,
        freshness=freshness,
        checks=checks,
    )
    blockers = [
        {"check": check.name, "detail": redact(check.detail)}
        for check in checks
        if not check.ok
    ]
    report = {
        "schema_version": 1,
        "generated_at": now.isoformat(),
        "ok": not blockers,
        "expected_release_sha": config.expected_sha,
        "expected_deployment": config.expected_deployment,
        "api_url": normalized_api or "<invalid>",
        "inputs": {
            "api_configured": bool(config.api_url),
            "postgres_dsn_configured": bool(config.postgres_dsn),
            "evidence_configured": bool(config.evidence_path),
            "bearer_token_configured": bool(config.bearer_token),
            "secret_values_redacted": True,
        },
        "checks": [
            {
                **asdict(check),
                "detail": redact(check.detail),
            }
            for check in checks
        ],
        "blockers": blockers,
    }
    return checks, report


def _load_evidence(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("evidence root must be a JSON object")
    return payload


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default=os.environ.get(API_URL_ENV, ""))
    parser.add_argument(
        "--postgres-dsn-env",
        default=POSTGRES_DSN_ENV,
        help="Environment variable containing the PostgreSQL DSN; the value is never printed.",
    )
    parser.add_argument(
        "--evidence",
        type=Path,
        default=Path(os.environ.get(EVIDENCE_PATH_ENV, "")),
    )
    parser.add_argument(
        "--expected-sha",
        default=os.environ.get(EXPECTED_SHA_ENV, ""),
    )
    parser.add_argument(
        "--bearer-token-env",
        default=BEARER_TOKEN_ENV,
        help="Optional environment variable containing the API bearer token.",
    )
    parser.add_argument("--expected-deployment", default="production")
    parser.add_argument("--freshness-hours", type=float, default=30.0)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--control-schema", default="data_plane")
    parser.add_argument("--raw-schema", default="fongniao_raw")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--allow-http",
        action="store_true",
        help="Permit HTTP only for an explicitly controlled non-production verification target.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    dsn = os.environ.get(args.postgres_dsn_env, "").strip()
    token = os.environ.get(args.bearer_token_env, "").strip()
    evidence_path = args.evidence
    config = GateConfig(
        api_url=args.api_url.strip(),
        postgres_dsn=dsn,
        evidence_path=evidence_path,
        expected_sha=args.expected_sha.strip().lower(),
        expected_deployment=args.expected_deployment.strip().lower(),
        freshness_hours=args.freshness_hours,
        timeout=args.timeout,
        bearer_token=token,
        control_schema=args.control_schema,
        raw_schema=args.raw_schema,
        allow_http=args.allow_http,
    )
    correlation_id = f"corr-live-production-data-{int(time.time())}"
    evidence: dict[str, Any] = {}
    preflight = validate_config(config)
    if all(check.ok for check in preflight):
        try:
            evidence = _load_evidence(evidence_path)
        except (OSError, ValueError, json.JSONDecodeError):
            evidence = {}

    preflight_ok = all(check.ok for check in preflight)
    if preflight_ok:
        http_client: HttpEvidenceClient = UrllibHttpEvidenceClient(
            config.api_url,
            timeout=config.timeout,
            bearer_token=config.bearer_token,
            correlation_id=correlation_id,
        )
    else:
        class _UnavailableHttp:
            def get_json(self, path: str) -> dict[str, Any]:
                raise RuntimeError(f"live input preflight failed before request to {path}")

        http_client = _UnavailableHttp()
    try:
        if not preflight_ok:
            raise ValueError("live input preflight failed before database connection")
        database_client: DatabaseEvidenceClient = PsycopgDatabaseEvidenceClient(
            config.postgres_dsn,
            control_schema=config.control_schema,
            raw_schema=config.raw_schema,
        )
    except (RuntimeError, ValueError) as exc:
        unavailable_message = str(exc)

        class _UnavailableDatabase:
            def collect(self) -> dict[str, Any]:
                raise RuntimeError(unavailable_message)

        database_client = _UnavailableDatabase()

    checks, report = evaluate_gate(
        config,
        http_client=http_client,
        database_client=database_client,
        evidence_payload=evidence,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if all(check.ok for check in checks):
        print(f"Live production data gate passed. report={args.output}")
        return 0
    print("Live production data gate failed:")
    for blocker in report["blockers"]:
        print(f"- {blocker['check']}: {blocker['detail']}")
    print(f"report={args.output}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

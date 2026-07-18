from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import yaml


@dataclass(frozen=True)
class DomainEvent:
    event_type: str
    payload: dict[str, Any]
    tenant_id: str
    aggregate_type: str
    aggregate_id: str
    aggregate_version: int
    partition_key: str
    correlation_id: str
    producer: str
    schema_ref: str
    event_version: int = 1
    event_id: str = field(default_factory=lambda: str(uuid4()))
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    published_at: datetime | None = None
    causation_id: str | None = None
    actor_ref: str | None = None
    policy_version: str | None = None
    sensitive_fields: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "event_version": self.event_version,
            "occurred_at": self.occurred_at.isoformat() if isinstance(self.occurred_at, datetime) else self.occurred_at,
            "published_at": self.published_at.isoformat() if isinstance(self.published_at, datetime) else self.published_at,
            "producer": self.producer,
            "tenant_id": self.tenant_id,
            "aggregate_type": self.aggregate_type,
            "aggregate_id": self.aggregate_id,
            "aggregate_version": self.aggregate_version,
            "partition_key": self.partition_key,
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "actor_ref": self.actor_ref,
            "policy_version": self.policy_version,
            "schema_ref": self.schema_ref,
            "sensitive_fields": self.sensitive_fields,
            "payload": self.payload,
        }


# --- Event Validator Loader & Engine ---

def deep_merge(dict1: dict[str, Any], dict2: dict[str, Any]) -> dict[str, Any]:
    result = dict1.copy()
    for k, v in dict2.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = deep_merge(result[k], v)
        elif k in result and isinstance(result[k], list) and isinstance(v, list):
            result[k] = list(set(result[k] + v))
        else:
            result[k] = v
    return result


class EventContractValidator:
    def __init__(self) -> None:
        self.catalog: dict[str, dict[str, Any]] = {}
        self.payloads: dict[str, dict[str, Any]] = {}
        self.definitions: dict[str, dict[str, Any]] = {}
        self._load_contracts()

    def _load_contracts(self) -> None:
        root_dir = Path(__file__).resolve().parents[2]
        events_v1_path = root_dir / "docs/events/ODAY_PLUS_ASSISTED_LISTING_INTAKE_EVENTS_V1.yaml"
        addendum_path = root_dir / "docs/events/ODAY_PLUS_ASSISTED_LISTING_INTAKE_EVENTS_V1_1_ADDENDUM.yaml"
        schemas_path = root_dir / "docs/events/ODAY_PLUS_ASSISTED_LISTING_INTAKE_EVENT_PAYLOAD_SCHEMAS_V1.yaml"

        # Load main events yaml to populate catalog and initial payloads
        if events_v1_path.exists():
            with open(events_v1_path, encoding="utf-8") as f:
                v1_data = yaml.safe_load(f) or {}
                for entry in v1_data.get("catalog", []):
                    self.catalog[entry["event_type"]] = entry
                self.payloads = deep_merge(self.payloads, v1_data.get("payloads", {}))

        # Load addendum yaml to extend catalog and payloads
        if addendum_path.exists():
            with open(addendum_path, encoding="utf-8") as f:
                addendum_data = yaml.safe_load(f) or {}
                for entry in addendum_data.get("catalog_additions", []):
                    self.catalog[entry["event_type"]] = entry
                self.payloads = deep_merge(self.payloads, addendum_data.get("payloads", {}))

        # Load schemas last to get definitions and detailed payloads
        if schemas_path.exists():
            with open(schemas_path, encoding="utf-8") as f:
                schemas_data = yaml.safe_load(f) or {}
                self.payloads = deep_merge(self.payloads, schemas_data.get("payloads", {}))
                self.definitions.update(schemas_data.get("definitions", {}))

    def validate_type(self, val: Any, expected_type: str | list[str]) -> bool:
        if isinstance(expected_type, list):
            return any(self.validate_type(val, t) for t in expected_type)
        if expected_type == "string":
            return isinstance(val, str)
        if expected_type == "integer":
            return isinstance(val, int) and not isinstance(val, bool)
        if expected_type == "number":
            return isinstance(val, (int, float)) and not isinstance(val, bool)
        if expected_type == "boolean":
            return isinstance(val, bool)
        if expected_type == "object":
            return isinstance(val, dict)
        if expected_type == "array":
            return isinstance(val, (list, tuple))
        if expected_type == "null":
            return val is None
        return False

    def validate_format(self, val: str, fmt: str) -> bool:
        if fmt == "uuid":
            try:
                UUID(val)
                return True
            except ValueError:
                return False
        if fmt == "date-time":
            pattern = r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$"
            return bool(re.match(pattern, val))
        if fmt == "uri":
            return val.startswith("http://") or val.startswith("https://") or val.startswith("gs://") or val.startswith("odp-artifact://")
        return True

    def validate_schema(self, data: Any, schema: dict[str, Any]) -> list[str]:
        errors = []

        if "$ref" in schema:
            ref_path = schema["$ref"]
            if ref_path.startswith("#/definitions/"):
                def_name = ref_path.split("/")[-1]
                if def_name in self.definitions:
                    return self.validate_schema(data, self.definitions[def_name])
                else:
                    errors.append(f"Definition '{def_name}' not found")
                    return errors
            else:
                errors.append(f"Unsupported ref: {ref_path}")
                return errors

        if "type" in schema:
            expected_type = schema["type"]
            if not self.validate_type(data, expected_type):
                errors.append(f"Expected type {expected_type}, got {type(data).__name__}")
                return errors

        if isinstance(data, dict):
            required = schema.get("required", [])
            for req in required:
                if req not in data:
                    errors.append(f"Missing required field: {req}")

            properties = schema.get("properties", {})
            for k, v in data.items():
                if k in properties:
                    sub_errors = self.validate_schema(v, properties[k])
                    for err in sub_errors:
                        errors.append(f"Field '{k}': {err}")
                elif schema.get("additionalProperties") is False:
                    errors.append(f"Additional property not allowed: {k}")

        elif isinstance(data, list):
            items_schema = schema.get("items")
            if items_schema:
                for idx, item in enumerate(data):
                    sub_errors = self.validate_schema(item, items_schema)
                    for err in sub_errors:
                        errors.append(f"Index {idx}: {err}")

        if "enum" in schema:
            enum_values = schema["enum"]
            if data not in enum_values:
                errors.append(f"Value '{data}' is not in enum {enum_values}")

        if "format" in schema and isinstance(data, str):
            fmt = schema["format"]
            if not self.validate_format(data, fmt):
                errors.append(f"Value '{data}' does not match format {fmt}")

        if "minimum" in schema and isinstance(data, (int, float)):
            min_val = schema["minimum"]
            if data < min_val:
                errors.append(f"Value {data} is less than minimum {min_val}")

        if "maximum" in schema and isinstance(data, (int, float)):
            max_val = schema["maximum"]
            if data > max_val:
                errors.append(f"Value {data} is greater than maximum {max_val}")

        return errors

    def validate_envelope(self, event_dict: dict[str, Any]) -> list[str]:
        required_fields = [
            "event_id", "event_type", "event_version", "occurred_at", "producer",
            "tenant_id", "aggregate_type", "aggregate_id", "aggregate_version",
            "partition_key", "correlation_id", "payload"
        ]
        errors = []
        for f in required_fields:
            if f not in event_dict or event_dict[f] is None:
                errors.append(f"Envelope missing required field: {f}")

        if errors:
            return errors

        # Validate types/formats of envelope fields
        for uuid_field in ["event_id", "tenant_id", "aggregate_id", "correlation_id"]:
            val = event_dict.get(uuid_field)
            if val and not self.validate_format(val, "uuid"):
                errors.append(f"Field '{uuid_field}' must be a valid UUID, got: {val}")

        for dt_field in ["occurred_at", "published_at"]:
            val = event_dict.get(dt_field)
            if val and not self.validate_format(val, "date-time"):
                errors.append(f"Field '{dt_field}' must be a valid date-time string, got: {val}")

        for int_field in ["event_version", "aggregate_version"]:
            val = event_dict.get(int_field)
            if val is not None and (not isinstance(val, int) or val < 1):
                errors.append(f"Field '{int_field}' must be an integer >= 1, got: {val}")

        for str_field in ["event_type", "producer", "aggregate_type", "partition_key"]:
            val = event_dict.get(str_field)
            if val is not None and not isinstance(val, str):
                errors.append(f"Field '{str_field}' must be a string, got: {val}")

        return errors

    def validate(self, event: DomainEvent | dict[str, Any]) -> list[str]:
        event_dict = event.to_dict() if isinstance(event, DomainEvent) else event
        errors = self.validate_envelope(event_dict)
        if errors:
            return errors

        event_type = event_dict["event_type"]
        if event_type not in self.catalog:
            errors.append(f"Event type '{event_type}' not found in catalog")
            return errors

        entry = self.catalog[event_type]
        schema_ref = entry["schema_ref"]
        # Resolve schema_ref
        schema_name = schema_ref.split("/")[-1]
        if schema_name not in self.payloads:
            errors.append(f"Payload schema '{schema_name}' for event '{event_type}' not found")
            return errors

        payload_schema = self.payloads[schema_name]
        payload_errors = self.validate_schema(event_dict["payload"], payload_schema)
        for err in payload_errors:
            errors.append(f"Payload: {err}")

        # Check sensitive fields configuration
        expected_sensitive = entry.get("sensitive_fields", [])
        actual_sensitive = event_dict.get("sensitive_fields", [])
        # We check that every expected sensitive field path is listed in sensitive_fields
        for sf in expected_sensitive:
            if sf not in actual_sensitive:
                errors.append(f"Expected sensitive field '{sf}' is not declared in sensitive_fields")

        return errors

# Global instance for validation
_validator = EventContractValidator()

def validate_event(event: DomainEvent | dict[str, Any]) -> list[str]:
    return _validator.validate(event)

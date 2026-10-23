"""Preserve the source/effective field history of an Intake correction."""

import re
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from modules.external_data.application.assisted_intake import CORRECTABLE_FIELDS


def _name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


INTERNAL_INPUTS = {_name(name) for name in CORRECTABLE_FIELDS} | {
    "addressraw",
    "rentamount",
    "area",
    "areaping",
    "listingtype",
    "listingstatus",
}
CLASSIFICATIONS = {"PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"}


def field_lineage(intake: Mapping[str, Any], field_path: str) -> dict[str, Any]:
    matches = [value for value in intake.get("fields", []) if value.get("field_path") == field_path]
    if len(matches) > 1:
        raise ValueError("Duplicate correction field lineage")
    if matches:
        field = deepcopy(matches[0])
        if field.get("classification") not in CLASSIFICATIONS:
            raise ValueError("Correction field classification is unavailable")
        if field.get("masked") or field["classification"] == "RESTRICTED":
            raise PermissionError("FIELD_MASKED: governed restricted correction is required")
        return field
    if _name(field_path) not in INTERNAL_INPUTS:
        raise ValueError("Correction field has no declared classification")
    return {"field_path": field_path, "classification": "INTERNAL", "masked": False}


def effective_value(field: Mapping[str, Any]) -> Any:
    if "effective" in field:
        return deepcopy(field["effective"])
    for key in ("corrected", "normalized", "parsed"):
        if key in field and field[key] is not None:
            return deepcopy(field[key])
    return None


def correction_values(
    intake: Mapping[str, Any], field_path: str, corrected_value: Any
) -> dict[str, Any]:
    field = field_lineage(intake, field_path)
    return {
        "field_path": field_path,
        "field_classification": field["classification"],
        "parsed_value": deepcopy(field.get("parsed")),
        "normalized_value": deepcopy(field.get("normalized")),
        "corrected_value": deepcopy(corrected_value),
        "before_effective_value": effective_value(field),
        "after_effective_value": deepcopy(corrected_value),
        "field_before": field,
        "source_snapshot_id": intake.get("source_snapshot_id"),
        "parser_run_id": intake.get("parser_run_id"),
    }


def apply_field(intake: dict[str, Any], field_path: str, corrected_value: Any) -> None:
    field = field_lineage(intake, field_path)
    field["corrected"] = deepcopy(corrected_value)
    field["effective"] = deepcopy(corrected_value)
    fields = intake.setdefault("fields", [])
    for index, current in enumerate(fields):
        if current.get("field_path") == field_path:
            fields[index] = field
            return
    fields.append(field)

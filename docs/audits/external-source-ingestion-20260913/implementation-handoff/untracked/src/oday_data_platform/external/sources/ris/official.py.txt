"""RIS ODRP014 wire contract, pinned to the provider's published API shape."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

RIS_API_ROOT = "https://www.ris.gov.tw/rs-opendata/api/v1/datastore/ODRP014"


def ris_source_uri(release_key: str) -> str:
    """Select a statistical month explicitly; never relabel current bytes."""
    if not re.fullmatch(r"\d{4}-\d{2}", release_key):
        raise ValueError("RIS release key must be YYYY-MM")
    month = datetime.strptime(release_key, "%Y-%m").replace(tzinfo=timezone.utc)
    roc_year = month.year - 1911
    if roc_year < 1:
        raise ValueError("RIS release must use a supported ROC year")
    return f"{RIS_API_ROOT}/{roc_year:03d}{month.month:02d}"


def last_completed_month() -> str:
    return (datetime.now(timezone.utc).replace(day=1) - timedelta(days=1)).strftime("%Y-%m")


def ris_response_rows(payload: Any) -> list[dict[str, Any]]:
    """Reject provider errors and incomplete pages before normalizing records."""
    if isinstance(payload, dict) and ("responseCode" in payload or "responseData" in payload):
        if payload.get("responseCode") != "OD-0101-S":
            raise ValueError("RIS API did not report successful data acquisition")
        rows = payload.get("responseData")
        if not isinstance(rows, list):
            raise ValueError("RIS responseData must be an array")
        if int(payload.get("pageDataSize", -1)) != len(rows):
            raise ValueError("RIS pageDataSize does not match the retained rows")
        page, total_pages = int(payload.get("page", 0)), int(payload.get("totalPage", 0))
        if not 1 <= page <= total_pages:
            raise ValueError("RIS pagination is invalid")
        return rows
    if isinstance(payload, list):
        return payload
    return [payload]


def normalize_odrp014(row: dict[str, Any], release_key: str) -> dict[str, Any]:
    """Map official columns without inventing absent counts or identifiers."""
    if "statistic_yyymm" not in row:
        return row
    if str(row["statistic_yyymm"]) != ris_source_uri(release_key).rsplit("/", 1)[1]:
        raise ValueError("RIS statistical month differs from the requested release")
    code = str(row.get("district_code", ""))
    if not re.fullmatch(r"\d{11}", code):
        raise ValueError("RIS village district_code must contain 11 digits")
    site = str(row.get("site_id", ""))
    if len(site) <= 3 or site[2] not in {"縣", "市"}:
        raise ValueError("RIS site_id must contain county and township names")
    normalized = dict(row)
    normalized.update(admin_code=code, county_code=code[:5], town_code=code[:8],
                      village_code=code, county_name=site[:3], town_name=site[3:])
    for target, source in [("household_count", "household_no"), ("population_total", "people_total"),
                           ("male_count", "people_total_m"), ("female_count", "people_total_f")]:
        if source not in row:
            raise ValueError(f"RIS missing required field {source}")
        normalized[target] = row[source]
    if any(key.startswith("people_age_") for key in row):
        age_brackets = {"0-14": 0, "15-64": 0, "65+": 0}
        sex_totals = {"m": 0, "f": 0}
        for age in range(101):
            age_key = f"{age:03d}" if age < 100 else "100up"
            bracket = "0-14" if age < 15 else "15-64" if age < 65 else "65+"
            for sex in ("m", "f"):
                field = f"people_age_{age_key}_{sex}"
                if field not in row:
                    raise ValueError(f"RIS incomplete age series: {field}")
                value = int(row[field])
                if value < 0:
                    raise ValueError("RIS negative age count")
                age_brackets[bracket] += value
                sex_totals[sex] += value
        if sex_totals != {"m": int(row["people_total_m"]), "f": int(row["people_total_f"])}:
            raise ValueError("RIS age-by-sex sums differ from reported totals")
        normalized["age_brackets"] = age_brackets
    return normalized

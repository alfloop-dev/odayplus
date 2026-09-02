#!/usr/bin/env python3
"""Schema and digest helpers for the immutable ODay Plus release manifest.

The release manifest is the artifact identity shared by dev, ephemeral
staging, and production.  A deployment may add environment metadata around a
manifest, but it must never rebuild or rewrite the manifest itself.  The
``manifest_digest`` field is the SHA-256 of the canonical JSON payload with
that field removed; this makes the self-described file independently
verifiable without creating a hash cycle.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

SUPPORTED_SCHEMA_VERSIONS = (1, 2)
CURRENT_SCHEMA_VERSION = 2
SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
IMAGE_DIGEST_PATTERN = re.compile(r"^.+@sha256:[0-9a-f]{64}$")
RELEASE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")
RELEASE_STATUSES = frozenset({"ready", "blocked"})

REQUIRED_FIELDS_V1 = (
    "schema_version",
    "release_id",
    "candidate_sha",
    "components",
    "migration_digest",
    "data_contract_digest",
    "source_policy_digest",
    "external_sources_expected_enabled",
    "sbom_refs",
    "signature_refs",
    "created_at",
    "created_by_workflow",
    "manifest_digest",
)

REQUIRED_FIELDS_V2 = REQUIRED_FIELDS_V1 + (
    "rollback_release",
)

REQUIRED_FIELDS = REQUIRED_FIELDS_V1
SNAPSHOT_FIELDS = (
    "id",
    "uri",
    "object_generation",
    "content_sha256",
    "data_contract_digest",
    "masked",
)

# ---------------------------------------------------------------------------
# Sources-off data-plane posture
# ---------------------------------------------------------------------------
#
# EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN §3/§9/§14 describe the standing
# posture of this product: all sixteen data sources stay disabled, no provider
# credential exists, and public egress stays default-deny until a per-source
# activation receipt is approved.  A release in that posture has no masked
# snapshot to bind because there is no ingested third-party data to mask -- but
# "no snapshot" must not become "no evidence".  ``sources_off_attestation`` is
# the data-plane evidence such a release carries instead, and it is only worth
# anything because it is *derived and bound* rather than declared:
#
# * it enumerates every source in :data:`EXTERNAL_SOURCE_INVENTORY`, so it
#   cannot be satisfied by a bare boolean or a short list;
# * ``binding_digest`` covers this candidate SHA, these component image
#   digests, and this source-policy digest, so an attestation cannot be lifted
#   from another release, replayed after a rebuild, or kept across a policy
#   change; and
# * nothing in the release path accepts a hand-supplied ``binding_digest`` --
#   the build phase computes it from the deployment posture it read at the
#   release SHA.
SOURCES_OFF_PROVIDER_MODE = "disabled"
SOURCES_OFF_EGRESS_POSTURE = "default-deny"
SOURCE_STATUS_DISABLED = "disabled"
SOURCE_EGRESS_DENIED = "denied"

#: The sixteen internal and external sources the rollout plan holds closed.
#: This mirrors the ODP-DEV-ROLLOUT-001 provider-off audit inventory; it is the
#: schema-side spelling of that same set, not a second source registry.
EXTERNAL_SOURCE_INVENTORY = (
    "store_master_snapshot",
    "machine_master_snapshot",
    "machine_cycle_event",
    "machine_status_event",
    "transaction_event",
    "price_schedule_snapshot",
    "maintenance_work_order_event",
    "customer_service_case_event",
    "poi_snapshot",
    "geocode_result_snapshot",
    "admin_boundary_snapshot",
    "listing_raw_snapshot",
    "competitor_store_snapshot",
    "demographics_snapshot",
    "weather_daily_snapshot",
    "store_opening_authority_snapshot",
)
EXPECTED_EXTERNAL_SOURCE_COUNT = len(EXTERNAL_SOURCE_INVENTORY)

#: Source-id words that name a *kind* of record rather than a source, so they
#: identify nothing on their own. They are dropped before a source id is
#: matched against a deployment environment variable name.
GENERIC_SOURCE_ID_TOKENS = frozenset(
    {"snapshot", "event", "raw", "result", "daily", "store"}
)

#: Name endings that make a deployment variable a *credential*, and the ones
#: that make it an *endpoint*.
#:
#: The release toolchain deliberately holds no provider inventory. Naming the
#: provider credentials here would restate ``modules/external_data`` inside
#: ``delivery_toolchain/release/``, which the external-data boundary
#: (``odayplus.legacy-external-data-disposition.v2``) forbids: that inventory is
#: the frozen registry's to hold, and only the registry, the deployment wiring
#: and the test suites are declared to carry it. So this module recognises
#: *shapes* — a source id's own words plus generic security nouns — and never
#: a provider. Anything attributed to a source that matches neither shape is
#: read as a credential, because an unrecognised secret must not be the reason
#: a sources-off release is admitted.
#: ``AUTH_STATUS`` sits in the credential list on purpose: it gates a
#: credential, so it is checked before the plainer ``STATUS`` posture flag.
CREDENTIAL_ENV_SUFFIXES = (
    "API_KEY",
    "TOKEN",
    "SECRET",
    "ATTESTATION",
    "AUTH_STATUS",
    "CREDENTIAL",
    "PASSWORD",
)
ENDPOINT_ENV_SUFFIXES = ("URL", "URI", "ENDPOINT", "HOST")

#: A source's posture flag. It carries no secret and grants no egress: it is
#: the deployment saying out loud whether the source is on. Reading it as a
#: credential would make the committed provider-off workflow undeployable,
#: which is the very coupling this task exists to remove.
STATUS_ENV_SUFFIXES = ("STATUS", "MODE", "ENABLED")


def source_id_env_tokens(source_id: str) -> frozenset[str]:
    """回傳足以指認這個 source 的字詞（大寫）。

    ``competitor_store_snapshot`` 會收斂成 ``{"COMPETITOR"}``；``store`` 這種
    多個 source 共用的字被丟掉，否則 ``store_opening_authority_snapshot`` 的
    變數會被誤算到 ``competitor_store_snapshot`` 頭上。若一個 source id 全部
    由通用字組成，就退回整組字詞，寧可比對得更嚴格也不要比對不到。
    """

    words = [part for part in source_id.split("_") if part]
    discriminating = [word for word in words if word not in GENERIC_SOURCE_ID_TOKENS]
    return frozenset(word.upper() for word in (discriminating or words))


def env_var_belongs_to_source(env_var: str, source_id: str) -> bool:
    """判斷一個 deployment 環境變數名稱是否屬於這個 source。

    只看名稱的字詞組成，不看 provider 是誰：一個變數會算到 ``poi_snapshot``
    頭上，是因為它的名稱帶了 ``POI`` 這個字，不是因為這裡記得哪一家 provider
    用什麼變數名。這也是這個模組能通過 external-data boundary 的原因。
    """

    return source_id_env_tokens(source_id) <= frozenset(env_var.upper().split("_"))


def classify_source_env_var(env_var: str) -> str:
    """把屬於某個 source 的環境變數分成 ``credential``、``endpoint`` 或 ``status``。

    順序有意義：``AUTH_STATUS`` 先被判成 credential，才輪到一般的 ``STATUS``
    被判成 posture flag。認不出來的字尾一律算 credential——看不懂的秘密不該
    成為 sources-off release 被放行的理由。
    """

    name = env_var.upper()
    if any(name.endswith(suffix) for suffix in CREDENTIAL_ENV_SUFFIXES):
        return "credential"
    if any(name.endswith(suffix) for suffix in ENDPOINT_ENV_SUFFIXES):
        return "endpoint"
    if any(name.endswith(suffix) for suffix in STATUS_ENV_SUFFIXES):
        return "status"
    return "credential"


SOURCE_POSTURE_FIELDS = ("source_id", "status", "credentials_present", "public_egress")
SOURCES_OFF_ATTESTATION_FIELDS = (
    "provider_mode",
    "egress_posture",
    "total_sources_audited",
    "all_sources_disabled",
    "zero_credentials_present",
    "sources_inventory",
    "egress_evidence",
    "binding_digest",
)



# The posture inventory is not allowed to turn the absence of an endpoint
# variable into a security verdict. A sources-off release must carry proof
# that the *single* Runtime Release path actually binds Cloud Run to the
# fail-closed VPC/firewall contract. These values are deliberately boring and
# secret-free; the digest is over the checked-in IaC/deploy entrypoint files.
SOURCES_OFF_EGRESS_EVIDENCE_FIELDS = (
    "kind",
    "cloud_run_egress",
    "firewall_egress",
    "workflow_vpc_binding",
    "deploy_entrypoint_vpc_binding",
    "runtime_probe_wiring",
    "runtime_probe",
    "runtime_probe_receipt",
    "resolved_cloud_run_egress",
    "runtime_probe_receipt_content_digest",
    "provider_credentials_runtime",
    "proof_source",
    "contract_digest",
)
SOURCES_OFF_EGRESS_EVIDENCE_KIND = "runtime-release-egress-contract"
SOURCES_OFF_CLOUD_RUN_EGRESS = "ALL_TRAFFIC"
SOURCES_OFF_FIREWALL_EGRESS = "default-deny"
SOURCES_OFF_PROVIDER_CREDENTIALS = "absent"
SOURCES_OFF_RUNTIME_PROBE = "public_egress_denied"
SOURCES_OFF_RUNTIME_PROBE_RECEIPT = ".odp_data/deployment/public-egress-probe.json"
SOURCES_OFF_RUNTIME_PROBE_RESULT = "passed"
SOURCES_OFF_RUNTIME_PROBE_REASON = "public_canary_denied"

# These are the checked-in contract inputs for the one Runtime Release path.
# Their digest makes the otherwise secret-free posture receipt specific to the
# workflow, deploy entrypoint, and firewall/IaC that will consume it.
SOURCES_OFF_EGRESS_CONTRACT_FILES = (
    ".github/workflows/deploy-dev.yml",
    "product_ops/deployment/deploy_cloud_run_waji.sh",
    "infra/terraform/cloud_run.tf",
    "infra/terraform/network.tf",
    "product_ops/deployment/staging_lifecycle.py",
    "product_ops/deployment/cloud_run_job_entrypoint.py",
)


def is_exact_sha(value: Any) -> bool:
    """Return whether *value* is a lowercase 40-character git SHA."""

    return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{40}", value))


def is_sha256_digest(value: Any) -> bool:
    return isinstance(value, str) and bool(SHA256_PATTERN.fullmatch(value))


def canonical_payload(manifest: dict[str, Any]) -> dict[str, Any]:
    """Return the immutable payload used for manifest identity hashing."""

    payload = copy.deepcopy(manifest)
    payload.pop("manifest_digest", None)
    return payload


def canonical_bytes(manifest: dict[str, Any]) -> bytes:
    return json.dumps(
        canonical_payload(manifest),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def compute_manifest_digest(manifest: dict[str, Any]) -> str:
    """Compute the self-describing SHA-256 manifest identity."""

    return "sha256:" + hashlib.sha256(canonical_bytes(manifest)).hexdigest()


def is_valid_timestamp(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _snapshot_errors(snapshot: Any, *, label: str) -> list[str]:
    """Validate a snapshot pointer that is embedded in a release identity."""

    if not isinstance(snapshot, dict):
        return [f"{label} must be an object"]

    errors: list[str] = []
    for field in SNAPSHOT_FIELDS:
        if field not in snapshot:
            errors.append(f"{label} missing required field: {field}")
    if "id" in snapshot and (
        not isinstance(snapshot["id"], str) or not snapshot["id"].strip()
    ):
        errors.append(f"{label}.id must be a non-empty string")
    if "uri" in snapshot and (
        not isinstance(snapshot["uri"], str) or not snapshot["uri"].strip()
    ):
        errors.append(f"{label}.uri must be a non-empty string")
    if "object_generation" in snapshot:
        generation = snapshot["object_generation"]
        valid_generation = (
            isinstance(generation, int)
            and not isinstance(generation, bool)
            and generation >= 0
        ) or (
            isinstance(generation, str) and bool(re.fullmatch(r"[0-9]+", generation))
        )
        if not valid_generation:
            errors.append(f"{label}.object_generation must be a non-negative integer")
    if "content_sha256" in snapshot and not is_sha256_digest(
        snapshot["content_sha256"]
    ):
        errors.append(
            f"{label}.content_sha256 must be a sha256:<64 lowercase hex> digest"
        )
    if "data_contract_digest" in snapshot and not is_sha256_digest(
        snapshot["data_contract_digest"]
    ):
        errors.append(
            f"{label}.data_contract_digest must be a sha256:<64 lowercase hex> digest"
        )
    if "masked" in snapshot and snapshot["masked"] is not True:
        errors.append(f"{label}.masked must be True")
    return errors


def _component_image_map(components: Any) -> dict[str, str]:
    """Return ``{component: image}`` for binding, ignoring shared-image metadata."""

    images: dict[str, str] = {}
    if not isinstance(components, dict):
        return images
    for name, component in components.items():
        if isinstance(component, dict):
            image = component.get("image")
        else:
            image = component
        if isinstance(name, str) and isinstance(image, str):
            images[name] = image
    return images


def sources_off_posture_payload(attestation: Any) -> dict[str, Any]:
    """Return the posture subset that ``binding_digest`` commits to."""

    if not isinstance(attestation, dict):
        return {}
    inventory = attestation.get("sources_inventory")
    normalised_inventory: list[dict[str, Any]] = []
    if isinstance(inventory, list):
        for entry in inventory:
            if not isinstance(entry, dict):
                continue
            normalised_inventory.append(
                {field: entry.get(field) for field in SOURCE_POSTURE_FIELDS}
            )
        normalised_inventory.sort(key=lambda entry: str(entry.get("source_id")))
    evidence = attestation.get("egress_evidence")
    normalised_evidence = (
        {field: evidence.get(field) for field in SOURCES_OFF_EGRESS_EVIDENCE_FIELDS}
        if isinstance(evidence, dict)
        else {}
    )
    return {
        "provider_mode": attestation.get("provider_mode"),
        "egress_posture": attestation.get("egress_posture"),
        "total_sources_audited": attestation.get("total_sources_audited"),
        "all_sources_disabled": attestation.get("all_sources_disabled"),
        "zero_credentials_present": attestation.get("zero_credentials_present"),
        "sources_inventory": normalised_inventory,
        "egress_evidence": normalised_evidence,
    }


def compute_sources_off_binding_digest(
    *,
    candidate_sha: Any,
    components: Any,
    source_policy_digest: Any,
    posture: dict[str, Any],
) -> str:
    """Bind a sources-off posture to one candidate, one image set, one policy.

    Without this the attestation would be a free-floating claim: copyable to the
    next candidate, still "valid" after the images were rebuilt, and unaffected
    by a source-policy change.  Binding it to all three is what makes the
    posture evidence about *this* release.
    """

    payload = {
        "candidate_sha": candidate_sha,
        "component_images": _component_image_map(components),
        "source_policy_digest": source_policy_digest,
        "external_sources_expected_enabled": [],
        "posture": posture,
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def build_sources_off_attestation(
    *,
    candidate_sha: str,
    components: dict[str, Any],
    source_policy_digest: str,
    provider_mode: str,
    sources_inventory: list[dict[str, Any]],
    egress_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Seal an observed sources-off posture into a bound attestation.

    The caller supplies what it *observed*; it does not get to supply the
    verdict fields or the digest.  ``all_sources_disabled``,
    ``zero_credentials_present`` and ``egress_posture`` are derived from the
    inventory here, so an attestation cannot claim a clean posture over a dirty
    inventory -- :func:`sources_off_attestation_errors` re-derives them and
    rejects the manifest if they disagree.
    """

    inventory = [
        {field: entry.get(field) for field in SOURCE_POSTURE_FIELDS}
        for entry in sources_inventory
    ]
    inventory.sort(key=lambda entry: str(entry.get("source_id")))
    if egress_evidence is None:
        egress_evidence = build_sources_off_egress_evidence()
    attestation: dict[str, Any] = {
        "provider_mode": provider_mode,
        "egress_posture": _derived_egress_posture(inventory),
        "total_sources_audited": len(inventory),
        "all_sources_disabled": _derived_all_disabled(inventory),
        "zero_credentials_present": _derived_zero_credentials(inventory),
        "sources_inventory": inventory,
        "egress_evidence": copy.deepcopy(egress_evidence),
    }
    attestation["binding_digest"] = compute_sources_off_binding_digest(
        candidate_sha=candidate_sha,
        components=components,
        source_policy_digest=source_policy_digest,
        posture=sources_off_posture_payload(attestation),
    )
    return attestation


def _derived_all_disabled(inventory: list[dict[str, Any]]) -> bool:
    return bool(inventory) and all(
        entry.get("status") == SOURCE_STATUS_DISABLED for entry in inventory
    )


def _derived_zero_credentials(inventory: list[dict[str, Any]]) -> bool:
    return all(entry.get("credentials_present") is False for entry in inventory)


def _derived_egress_posture(inventory: list[dict[str, Any]]) -> str:
    if inventory and all(
        entry.get("public_egress") == SOURCE_EGRESS_DENIED for entry in inventory
    ):
        return SOURCES_OFF_EGRESS_POSTURE
    return "provider-egress-allowed"


def sources_off_attestation_errors(
    attestation: Any,
    *,
    candidate_sha: Any = None,
    components: Any = None,
    source_policy_digest: Any = None,
    label: str = "manifest.sources_off_attestation",
) -> list[str]:
    """Return why *attestation* is not admissible sources-off data-plane evidence.

    Every branch here is a fail-closed condition named by the rollout plan: a
    provider mode that is not ``disabled``, a source that is not disabled, a
    provider credential that exists, egress that is not default-deny, or a
    binding that does not belong to this release.
    """

    if not isinstance(attestation, dict):
        return [f"{label} must be an object"]

    errors: list[str] = []
    for field in SOURCES_OFF_ATTESTATION_FIELDS:
        if field not in attestation:
            errors.append(f"{label} missing required field: {field}")

    provider_mode = attestation.get("provider_mode")
    if provider_mode != SOURCES_OFF_PROVIDER_MODE:
        errors.append(
            f"{label}.provider_mode must be {SOURCES_OFF_PROVIDER_MODE!r}; "
            f"got {provider_mode!r}"
        )

    inventory = attestation.get("sources_inventory")
    if not isinstance(inventory, list):
        errors.append(f"{label}.sources_inventory must be a list")
        inventory = []
    else:
        observed_ids: list[str] = []
        for index, entry in enumerate(inventory):
            entry_label = f"{label}.sources_inventory[{index}]"
            if not isinstance(entry, dict):
                errors.append(f"{entry_label} must be an object")
                continue
            source_id = entry.get("source_id")
            if isinstance(source_id, str):
                observed_ids.append(source_id)
            else:
                errors.append(f"{entry_label}.source_id must be a string")
            if entry.get("status") != SOURCE_STATUS_DISABLED:
                errors.append(
                    f"{entry_label}.status must be {SOURCE_STATUS_DISABLED!r} for a "
                    f"sources-off release; got {entry.get('status')!r}"
                )
            if entry.get("credentials_present") is not False:
                errors.append(
                    f"{entry_label}.credentials_present must be False; a sources-off "
                    "release must carry no provider credential"
                )
            if entry.get("public_egress") != SOURCE_EGRESS_DENIED:
                errors.append(
                    f"{entry_label}.public_egress must be {SOURCE_EGRESS_DENIED!r}; "
                    "a sources-off release must keep public egress default-deny"
                )
        missing = sorted(set(EXTERNAL_SOURCE_INVENTORY) - set(observed_ids))
        unexpected = sorted(set(observed_ids) - set(EXTERNAL_SOURCE_INVENTORY))
        duplicated = sorted({sid for sid in observed_ids if observed_ids.count(sid) > 1})
        if missing:
            errors.append(
                f"{label}.sources_inventory does not audit every source; missing: "
                + ", ".join(missing)
            )
        if unexpected:
            errors.append(
                f"{label}.sources_inventory audits sources that are not in the "
                "canonical inventory: " + ", ".join(unexpected)
            )
        if duplicated:
            errors.append(
                f"{label}.sources_inventory repeats sources: " + ", ".join(duplicated)
            )

    normalised = sources_off_posture_payload(attestation)["sources_inventory"]
    if attestation.get("total_sources_audited") != EXPECTED_EXTERNAL_SOURCE_COUNT:
        errors.append(
            f"{label}.total_sources_audited must be {EXPECTED_EXTERNAL_SOURCE_COUNT}; "
            f"got {attestation.get('total_sources_audited')!r}"
        )
    if attestation.get("all_sources_disabled") is not _derived_all_disabled(normalised):
        errors.append(
            f"{label}.all_sources_disabled does not match its own sources_inventory"
        )
    if attestation.get("zero_credentials_present") is not _derived_zero_credentials(
        normalised
    ):
        errors.append(
            f"{label}.zero_credentials_present does not match its own sources_inventory"
        )
    derived_egress = _derived_egress_posture(normalised)
    if attestation.get("egress_posture") != derived_egress:
        errors.append(
            f"{label}.egress_posture does not match its own sources_inventory"
        )
    elif derived_egress != SOURCES_OFF_EGRESS_POSTURE:
        errors.append(
            f"{label}.egress_posture must be {SOURCES_OFF_EGRESS_POSTURE!r} for a "
            f"sources-off release; got {derived_egress!r}"
        )

    evidence = attestation.get("egress_evidence")
    if not isinstance(evidence, dict):
        errors.append(f"{label}.egress_evidence must be an object")
        evidence = {}
    for field in SOURCES_OFF_EGRESS_EVIDENCE_FIELDS:
        if field not in evidence:
            errors.append(f"{label}.egress_evidence missing required field: {field}")
    expected_evidence = build_sources_off_egress_evidence()
    for field in SOURCES_OFF_EGRESS_EVIDENCE_FIELDS:
        if evidence.get(field) != expected_evidence.get(field):
            errors.append(
                f"{label}.egress_evidence.{field} is not the checked-in Runtime "
                "Release egress contract"
            )
    if evidence.get("resolved_cloud_run_egress") != evidence.get("cloud_run_egress"):
        errors.append(
            f"{label}.egress_evidence.resolved_cloud_run_egress must match the "
            "resolved Cloud Run egress bound by the workflow"
        )
    if evidence.get("runtime_probe_receipt_content_digest") != compute_sources_off_probe_receipt_content_digest(
        resolved_cloud_run_egress=evidence.get("resolved_cloud_run_egress"),
    ):
        errors.append(
            f"{label}.egress_evidence.runtime_probe_receipt_content_digest must "
            "bind the expected probe receipt content"
        )
    errors.extend(_sources_off_egress_contract_errors())

    recorded_binding = attestation.get("binding_digest")
    if not is_sha256_digest(recorded_binding):
        errors.append(f"{label}.binding_digest must be a sha256:<64 lowercase hex> digest")
    elif candidate_sha is not None or components is not None:
        expected_binding = compute_sources_off_binding_digest(
            candidate_sha=candidate_sha,
            components=components,
            source_policy_digest=source_policy_digest,
            posture=sources_off_posture_payload(attestation),
        )
        if recorded_binding != expected_binding:
            errors.append(
                f"{label}.binding_digest is not bound to this release's candidate "
                "SHA, component image digests, and source_policy_digest"
            )
    return errors


def validate_manifest(
    manifest: Any,
    *,
    expected_candidate_sha: str | None = None,
    expected_digest: str | None = None,
) -> list[str]:
    """Return all manifest integrity errors; an empty list means valid.

    The validator deliberately requires every deployment identity field.  It
    does not accept mutable tags, abbreviated SHAs, or an omitted digest as a
    substitute for an immutable artifact reference.
    """

    errors: list[str] = []
    if not isinstance(manifest, dict):
        return ["manifest must be a JSON object"]

    version = manifest.get("schema_version")
    if isinstance(version, bool) or version not in SUPPORTED_SCHEMA_VERSIONS:
        errors.append(
            f"manifest.schema_version must be one of {list(SUPPORTED_SCHEMA_VERSIONS)}, "
            f"got: {version!r}"
        )
        required_fields = REQUIRED_FIELDS_V1
    elif version == 2:
        required_fields = REQUIRED_FIELDS_V2
    else:
        required_fields = REQUIRED_FIELDS_V1

    for field in required_fields:
        if field not in manifest:
            errors.append(f"manifest missing required field: {field}")

    # Schema v2 requires *data-plane evidence*, and there are exactly two kinds.
    # A release that expects enabled external sources binds the approved masked
    # snapshot; a sources-off release binds a derived provider-off posture. The
    # two are mutually exclusive on purpose: carrying both is how a sources-off
    # claim would be used to talk past a snapshot binding that already exists.
    sources_enabled = manifest.get("external_sources_expected_enabled")
    attestation = manifest.get("sources_off_attestation")
    has_snapshot = manifest.get("data_snapshot") is not None
    if version == 2:
        if isinstance(sources_enabled, list) and sources_enabled:
            if not has_snapshot:
                errors.append(
                    "manifest missing required field: data_snapshot; a release that "
                    "expects enabled external sources must bind the approved masked "
                    "snapshot"
                )
            if attestation is not None:
                errors.append(
                    "manifest.sources_off_attestation must not appear on a manifest "
                    "whose external_sources_expected_enabled is non-empty"
                )
        elif has_snapshot and attestation is not None:
            errors.append(
                "manifest.sources_off_attestation must not accompany "
                "manifest.data_snapshot; a sources-off posture may not override an "
                "existing snapshot binding"
            )
        elif not has_snapshot and attestation is None:
            errors.append(
                "manifest missing required field: data_snapshot; a sources-off "
                "release must instead bind manifest.sources_off_attestation"
            )

    if attestation is not None:
        errors.extend(
            sources_off_attestation_errors(
                attestation,
                candidate_sha=manifest.get("candidate_sha"),
                components=manifest.get("components"),
                source_policy_digest=manifest.get("source_policy_digest"),
            )
        )

    release_id = manifest.get("release_id")
    if not isinstance(release_id, str) or not RELEASE_ID_PATTERN.fullmatch(release_id):
        errors.append("manifest.release_id must be a stable release identifier")

    candidate_sha = manifest.get("candidate_sha")
    if not is_exact_sha(candidate_sha):
        errors.append(
            "manifest.candidate_sha must be an exact 40-character lowercase git SHA"
        )
    if expected_candidate_sha and candidate_sha != expected_candidate_sha:
        errors.append(
            "manifest.candidate_sha does not match release.candidate_sha; "
            "the manifest is for a different candidate"
        )

    # A candidate that never produced an image has no honest component list.
    # Forcing one here is what makes a manifest quote a *previous* candidate's
    # digests, so an empty component set is representable -- but only on a
    # manifest that records why, and never on an admissible one.
    components = manifest.get("components")
    if not isinstance(components, dict):
        errors.append("manifest.components must be an object")
    elif not components and manifest.get("release_status") != "blocked":
        errors.append(
            "manifest.components must be a non-empty object unless the manifest "
            "records release_status='blocked'"
        )
    elif not all(isinstance(name, str) and name.strip() for name in components):
        errors.append("manifest.components names must be non-empty strings")
    else:
        for name, component in components.items():
            label = f"manifest.components[{name!r}]"
            if not isinstance(component, dict):
                errors.append(f"{label} must be an object")
                continue
            image = component.get("image")
            if not isinstance(image, str) or not IMAGE_DIGEST_PATTERN.fullmatch(image):
                errors.append(
                    f"{label}.image must be an immutable image reference with @sha256 digest"
                )

    for field in ("migration_digest", "data_contract_digest", "source_policy_digest"):
        if not is_sha256_digest(manifest.get(field)):
            errors.append(f"manifest.{field} must be a sha256:<64 lowercase hex> digest")

    sources = manifest.get("external_sources_expected_enabled")
    if not isinstance(sources, list) or not all(
        isinstance(source, str) and source.strip() for source in sources
    ):
        errors.append(
            "manifest.external_sources_expected_enabled must be a list of non-empty strings"
        )

    for field in ("sbom_refs", "signature_refs"):
        refs = manifest.get(field)
        if not isinstance(refs, list) or not all(
            isinstance(ref, str) and ref.strip() for ref in refs
        ):
            errors.append(f"manifest.{field} must be a list of non-empty strings")

    release_status = manifest.get("release_status")
    if release_status is not None and release_status not in RELEASE_STATUSES:
        errors.append(
            f"manifest.release_status must be one of {sorted(RELEASE_STATUSES)}, "
            f"got: {release_status!r}"
        )
    if release_status == "blocked":
        blockers = manifest.get("blockers")
        if not isinstance(blockers, list) or not blockers:
            errors.append("blocked manifest must include a non-empty blockers list")

    if not is_valid_timestamp(manifest.get("created_at")):
        errors.append("manifest.created_at must be an RFC3339 timestamp with timezone")
    if not isinstance(manifest.get("created_by_workflow"), str) or not manifest.get(
        "created_by_workflow"
    ).strip():
        errors.append("manifest.created_by_workflow must be a non-empty workflow reference")

    # Validate data_snapshot if present. Schema v2 makes this field required;
    # v1 keeps it optional so historical manifests remain auditable.
    data_snapshot = manifest.get("data_snapshot")
    if data_snapshot is not None:
        errors.extend(_snapshot_errors(data_snapshot, label="manifest.data_snapshot"))
        if (
            isinstance(data_snapshot, dict)
            and is_sha256_digest(data_snapshot.get("data_contract_digest"))
            and is_sha256_digest(manifest.get("data_contract_digest"))
            and data_snapshot["data_contract_digest"] != manifest["data_contract_digest"]
        ):
            errors.append(
                "manifest.data_snapshot.data_contract_digest does not match "
                "manifest.data_contract_digest"
            )

    # Validate rollback_release if present
    rollback_release = manifest.get("rollback_release")
    if rollback_release is not None:
        if not isinstance(rollback_release, dict):
            errors.append("manifest.rollback_release must be an object")
        else:
            for rb_field in ("release_id", "candidate_sha", "manifest_digest", "components"):
                if rb_field not in rollback_release:
                    errors.append(f"manifest.rollback_release missing required field: {rb_field}")
            rb_release_id = rollback_release.get("release_id")
            if not isinstance(rb_release_id, str) or not RELEASE_ID_PATTERN.fullmatch(rb_release_id):
                errors.append("manifest.rollback_release.release_id must be a stable release identifier")
            rb_candidate_sha = rollback_release.get("candidate_sha")
            if not is_exact_sha(rb_candidate_sha):
                errors.append("manifest.rollback_release.candidate_sha must be an exact 40-character lowercase git SHA")
            elif rb_candidate_sha == candidate_sha:
                errors.append("manifest.rollback_release.candidate_sha must not match current candidate_sha; rollback must be a distinct release candidate")
            rb_digest = rollback_release.get("manifest_digest")
            if not is_sha256_digest(rb_digest):
                errors.append("manifest.rollback_release.manifest_digest must be a sha256:<64 lowercase hex> digest")

            rb_components = rollback_release.get("components")
            if not isinstance(rb_components, dict) or not rb_components:
                errors.append("manifest.rollback_release.components must be a non-empty object")
            else:
                for req_comp in ("api", "web"):
                    if req_comp not in rb_components:
                        errors.append(f"manifest.rollback_release.components missing required component: {req_comp!r}")
                for name, comp in rb_components.items():
                    label = f"manifest.rollback_release.components[{name!r}]"
                    if isinstance(comp, dict):
                        img = comp.get("image")
                    elif isinstance(comp, str):
                        img = comp
                    else:
                        errors.append(f"{label} must be an object or string reference")
                        continue
                    if not isinstance(img, str) or not IMAGE_DIGEST_PATTERN.fullmatch(img):
                        errors.append(f"{label}.image must be an immutable image reference with @sha256 digest")

            snapshot_key = next(
                (
                    key
                    for key in ("data_snapshot", "snapshot_pointer", "snapshot")
                    if key in rollback_release
                ),
                None,
            )
            if snapshot_key is not None:
                errors.extend(
                    _snapshot_errors(
                        rollback_release[snapshot_key],
                        label="manifest.rollback_release.data_snapshot",
                    )
                )
            else:
                # A rollback target that ran sources-off has no snapshot pointer
                # to carry forward, so it carries the binding digest of the
                # posture it was admitted on instead. A bare "it was sources
                # off" flag would be forgeable and is not accepted.
                rb_attestation = rollback_release.get("sources_off_attestation")
                if rb_attestation is None:
                    errors.append(
                        "manifest.rollback_release missing required data_snapshot "
                        "pointer or sources_off_attestation binding"
                    )
                elif not isinstance(rb_attestation, dict):
                    errors.append(
                        "manifest.rollback_release.sources_off_attestation must be an object"
                    )
                elif not is_sha256_digest(rb_attestation.get("binding_digest")):
                    errors.append(
                        "manifest.rollback_release.sources_off_attestation.binding_digest "
                        "must be a sha256:<64 lowercase hex> digest"
                    )

    recorded_digest = manifest.get("manifest_digest")
    if not is_sha256_digest(recorded_digest):
        errors.append("manifest.manifest_digest must be a sha256:<64 lowercase hex> digest")
    else:
        actual_digest = compute_manifest_digest(manifest)
        if recorded_digest != actual_digest:
            errors.append(
                "manifest.manifest_digest does not match its canonical immutable payload"
            )
        if expected_digest and recorded_digest != expected_digest:
            errors.append(
                "manifest.manifest_digest does not match the digest recorded by the registry"
            )

    return errors


def validate_release_admission(manifest: Any) -> list[str]:
    """Return why a structurally valid manifest cannot be deployed.

    A blocked manifest is intentionally still hashable and reviewable: it is
    the immutable record of what was observed and why release stopped.  It is
    not, however, a deployable artifact.  Keeping this predicate separate from
    ``validate_manifest`` lets auditors inspect a blocked candidate without
    accidentally treating it as a successful release.
    """

    errors = validate_manifest(manifest)
    if not isinstance(manifest, dict):
        return errors
    # Manifests created before the status field was introduced remain
    # admissible when they contain the required immutable references.  New
    # manifests must explicitly move to ``ready`` before deployment; a
    # recorded ``blocked`` state can never be promoted implicitly.
    release_status = manifest.get("release_status", "ready")
    if release_status != "ready":
        errors.append(
            "release admission requires manifest.release_status='ready'; "
            f"got {release_status!r}"
        )
    # ``validate_manifest`` lets a blocked manifest carry no components. That
    # relaxation must not travel into admission: a release with nothing to
    # deploy is not a release, whatever its recorded status says.
    components = manifest.get("components")
    if not isinstance(components, dict) or not components:
        errors.append(
            "release admission requires at least one immutable component image "
            "in manifest.components"
        )
    for field in ("sbom_refs", "signature_refs"):
        refs = manifest.get(field)
        if not isinstance(refs, list) or not refs:
            errors.append(f"release admission requires non-empty manifest.{field}")

    # Staging and production admission require data-plane evidence bound to this
    # release. Which evidence is required follows from the declared source
    # posture, and neither branch is optional.
    sources_enabled = manifest.get("external_sources_expected_enabled")
    attestation = manifest.get("sources_off_attestation")
    if isinstance(sources_enabled, list) and sources_enabled:
        if manifest.get("data_snapshot") is None:
            errors.append(
                "release admission with enabled external sources requires "
                "manifest.data_snapshot with masked=true and verified content sha256"
            )
    elif manifest.get("data_snapshot") is None and attestation is None:
        errors.append(
            "release admission requires manifest.data_snapshot with masked=true and "
            "verified content sha256, or manifest.sources_off_attestation bound to "
            "this candidate for a sources-off release"
        )

    # Anti-downgrade. Once a release has been admitted on a masked snapshot, the
    # next release cannot escape snapshot verification by declaring itself
    # sources-off: the rollback binding still names the snapshot the previous
    # release was admitted on, and a posture attestation does not supersede it.
    rollback_release = manifest.get("rollback_release")
    if attestation is not None and isinstance(rollback_release, dict):
        if any(
            key in rollback_release
            for key in ("data_snapshot", "snapshot_pointer", "snapshot")
        ):
            errors.append(
                "release admission refuses manifest.sources_off_attestation on a "
                "release whose rollback_release still binds a data_snapshot; a "
                "sources-off posture may not override an existing snapshot binding"
            )
    if manifest.get("rollback_release") is None:
        errors.append(
            "release admission requires manifest.rollback_release with verified candidate sha and components"
        )
    return errors


def component_binding_errors(manifest: Any, images: dict[str, str]) -> list[str]:
    """Return why *images* are not the artifacts this manifest identifies.

    A lease authorises deploying *a release*, not *any image*.  The Runtime
    Release deploy phase receives its image references as workflow inputs, so
    without this check a valid lease would admit an arbitrary digest that the
    build phase never produced, signed, or recorded an SBOM for.  Binding the
    handoff back to ``manifest.components`` is what makes "build once, deploy
    that exact artifact" an enforced property rather than a convention.
    """

    if not isinstance(manifest, dict):
        return ["manifest must be a JSON object"]
    components = manifest.get("components")
    if not isinstance(components, dict) or not components:
        return ["manifest.components must be a non-empty object"]

    errors: list[str] = []
    for name in sorted(images):
        image = images[name]
        if not isinstance(image, str) or not IMAGE_DIGEST_PATTERN.fullmatch(image):
            errors.append(
                f"handoff image for {name!r} must be an immutable @sha256 reference"
            )
            continue
        component = components.get(name)
        if not isinstance(component, dict):
            errors.append(
                f"manifest has no component {name!r}; this release never built that artifact"
            )
            continue
        if component.get("image") != image:
            errors.append(
                f"handoff image for {name!r} is not the image recorded by the manifest; "
                "the deploy would run an artifact this release did not build"
            )
    return errors


def load_manifest(
    path: Path,
    *,
    expected_candidate_sha: str | None = None,
    expected_digest: str | None = None,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Load and validate one manifest, returning errors instead of guessing."""

    if not path.exists():
        return None, [f"manifest file does not exist: {path}"]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, [f"manifest cannot be read as JSON: {exc}"]
    errors = validate_manifest(
        payload,
        expected_candidate_sha=expected_candidate_sha,
        expected_digest=expected_digest,
    )
    return (payload if isinstance(payload, dict) else None), errors


ROOT = Path(__file__).resolve().parents[2]


def compute_file_set_digest(paths: Any, *, root: Path = ROOT) -> str:
    """Compute deterministic SHA-256 digest over a sequence of file paths."""
    h = hashlib.sha256()
    for p in sorted(paths):
        path_obj = Path(p)
        if path_obj.is_file():
            rel_path = path_obj.relative_to(root).as_posix().encode("utf-8")
            h.update(rel_path)
            h.update(b"\x00")
            h.update(path_obj.read_bytes())
            h.update(b"\x00")
    return "sha256:" + h.hexdigest()


def compute_sources_off_egress_contract_digest(root: Path = ROOT) -> str:
    """Hash the checked-in Runtime Release egress contract inputs."""

    return compute_file_set_digest(
        (root / relative_path for relative_path in SOURCES_OFF_EGRESS_CONTRACT_FILES),
        root=root,
    )


def build_sources_off_egress_evidence(
    *,
    workflow_vpc_binding: bool = True,
    deploy_entrypoint_vpc_binding: bool = True,
    runtime_probe_wiring: bool = True,
    resolved_cloud_run_egress: str = SOURCES_OFF_CLOUD_RUN_EGRESS,
    provider_credentials_runtime: str = SOURCES_OFF_PROVIDER_CREDENTIALS,
    root: Path = ROOT,
) -> dict[str, Any]:
    """Derive the secret-free proof attached to a sources-off attestation.

    The workflow reader supplies the two binding observations and the builder
    supplies the contract digest.  The manifest validator independently checks
    the same checked-in inputs, so these fields are evidence, not a caller's
    free-form override.
    """

    resolved_egress = str(resolved_cloud_run_egress).strip()
    receipt_digest = compute_sources_off_probe_receipt_content_digest(
        resolved_cloud_run_egress=resolved_egress,
    )
    return {
        "kind": SOURCES_OFF_EGRESS_EVIDENCE_KIND,
        "cloud_run_egress": (
            SOURCES_OFF_CLOUD_RUN_EGRESS if workflow_vpc_binding else "unbound"
        ),
        "firewall_egress": (
            SOURCES_OFF_FIREWALL_EGRESS
            if deploy_entrypoint_vpc_binding
            else "unverified"
        ),
        "workflow_vpc_binding": "verified" if workflow_vpc_binding else "unbound",
        "deploy_entrypoint_vpc_binding": (
            "verified" if deploy_entrypoint_vpc_binding else "unbound"
        ),
        "runtime_probe_wiring": "verified" if runtime_probe_wiring else "unbound",
        "runtime_probe": SOURCES_OFF_RUNTIME_PROBE,
        "runtime_probe_receipt": SOURCES_OFF_RUNTIME_PROBE_RECEIPT,
        "resolved_cloud_run_egress": resolved_egress,
        "runtime_probe_receipt_content_digest": receipt_digest,
        "provider_credentials_runtime": provider_credentials_runtime,
        "proof_source": list(SOURCES_OFF_EGRESS_CONTRACT_FILES),
        "contract_digest": compute_sources_off_egress_contract_digest(root=root),
    }


def compute_sources_off_probe_receipt_content_digest(
    *,
    resolved_cloud_run_egress: str = SOURCES_OFF_CLOUD_RUN_EGRESS,
    result: str = SOURCES_OFF_RUNTIME_PROBE_RESULT,
    reason: str = SOURCES_OFF_RUNTIME_PROBE_REASON,
) -> str:
    """Hash the semantic, secret-free fields emitted by the live probe.

    Execution names and timestamps are intentionally excluded because they are
    run-scoped. The digest still binds the result, denial reason, and the
    egress value actually read back from the candidate job.
    """

    payload = {
        "expected": "denied",
        "reason": reason,
        "receipt_kind": "public_egress_probe",
        "result": result,
        "vpc_egress": resolved_cloud_run_egress,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def validate_sources_off_probe_receipt(
    receipt: Any,
    *,
    expected_candidate_sha: Any = None,
    expected_manifest_digest: Any = None,
    expected_egress: str = SOURCES_OFF_CLOUD_RUN_EGRESS,
) -> list[str]:
    """Validate the secret-free receipt emitted by the live egress probe.

    The manifest carries the expected semantic digest because the probe runs
    after admission.  This verifier checks the *actual* candidate-job
    readback and receipt body before the deployment can remain successful;
    timestamps and Cloud Run execution names are deliberately not part of the
    semantic digest because they are run-scoped.
    """

    label = "sources-off egress probe receipt"
    if not isinstance(receipt, dict):
        return [f"{label} must be an object"]

    required = (
        "schema_version",
        "receipt_kind",
        "secret_values_redacted",
        "candidate_sha",
        "manifest_digest",
        "job",
        "probe_url",
        "expected",
        "vpc_egress",
        "result",
        "reason",
        "execution",
        "recorded_at",
        "receipt_content_digest",
    )
    errors = [f"{label} missing required field: {field}" for field in required if field not in receipt]

    if receipt.get("schema_version") != 1:
        errors.append(f"{label}.schema_version must be 1")
    if receipt.get("receipt_kind") != "public_egress_probe":
        errors.append(f"{label}.receipt_kind must be 'public_egress_probe'")
    if receipt.get("secret_values_redacted") is not True:
        errors.append(f"{label}.secret_values_redacted must be True")

    candidate_sha = receipt.get("candidate_sha")
    if not is_exact_sha(candidate_sha):
        errors.append(f"{label}.candidate_sha must be an exact 40-character lowercase git SHA")
    if expected_candidate_sha is not None and candidate_sha != expected_candidate_sha:
        errors.append(f"{label}.candidate_sha does not match the deployed candidate SHA")

    manifest_digest = receipt.get("manifest_digest")
    if not is_sha256_digest(manifest_digest):
        errors.append(f"{label}.manifest_digest must be a sha256:<64 lowercase hex> digest")
    if expected_manifest_digest is not None and manifest_digest != expected_manifest_digest:
        errors.append(f"{label}.manifest_digest does not match the admitted manifest digest")

    if not isinstance(receipt.get("job"), str) or not receipt["job"].strip():
        errors.append(f"{label}.job must be a non-empty string")
    if receipt.get("probe_url") != "https://example.com/":
        errors.append(f"{label}.probe_url must be the fixed public deny canary")
    if receipt.get("expected") != "denied":
        errors.append(f"{label}.expected must be 'denied'")
    if receipt.get("vpc_egress") != expected_egress:
        errors.append(
            f"{label}.vpc_egress must be {expected_egress!r}; "
            f"got {receipt.get('vpc_egress')!r}"
        )
    if receipt.get("result") != SOURCES_OFF_RUNTIME_PROBE_RESULT:
        errors.append(f"{label}.result must be {SOURCES_OFF_RUNTIME_PROBE_RESULT!r}")
    if receipt.get("reason") != SOURCES_OFF_RUNTIME_PROBE_REASON:
        errors.append(f"{label}.reason must be {SOURCES_OFF_RUNTIME_PROBE_REASON!r}")
    if receipt.get("execution") != "succeeded":
        errors.append(f"{label}.execution must be 'succeeded'")
    if not is_valid_timestamp(receipt.get("recorded_at")):
        errors.append(f"{label}.recorded_at must be an RFC3339 timestamp with timezone")

    recorded_content_digest = receipt.get("receipt_content_digest")
    if not is_sha256_digest(recorded_content_digest):
        errors.append(
            f"{label}.receipt_content_digest must be a sha256:<64 lowercase hex> digest"
        )
    else:
        expected_content_digest = compute_sources_off_probe_receipt_content_digest(
            resolved_cloud_run_egress=receipt.get("vpc_egress"),
            result=receipt.get("result"),
            reason=receipt.get("reason"),
        )
        if recorded_content_digest != expected_content_digest:
            errors.append(
                f"{label}.receipt_content_digest does not match the receipt's semantic content"
            )
    return errors


def _sources_off_egress_contract_errors(root: Path = ROOT) -> list[str]:
    """Check the concrete VPC/firewall contract behind a posture receipt."""

    errors: list[str] = []
    paths = {relative: root / relative for relative in SOURCES_OFF_EGRESS_CONTRACT_FILES}
    missing = [relative for relative, path in paths.items() if not path.is_file()]
    if missing:
        return [
            "sources-off egress contract is incomplete; missing: " + ", ".join(missing)
        ]

    workflow = paths[".github/workflows/deploy-dev.yml"].read_text(encoding="utf-8")
    if "ODP_EXTERNAL_PROVIDER_MODE: disabled" not in workflow:
        errors.append("deploy workflow does not fix ODP_EXTERNAL_PROVIDER_MODE to disabled")
    if "ODP_CLOUD_RUN_VPC_CONNECTOR:" not in workflow:
        errors.append("deploy workflow does not bind ODP_CLOUD_RUN_VPC_CONNECTOR")
    if "ODP_CLOUD_RUN_VPC_EGRESS:" not in workflow:
        errors.append("deploy workflow does not bind ODP_CLOUD_RUN_VPC_EGRESS")
    if (
        f"PUBLIC_EGRESS_PROBE_REPORT: {SOURCES_OFF_RUNTIME_PROBE_RECEIPT}"
        not in workflow
    ):
        errors.append("deploy workflow does not retain the public egress probe receipt")

    deploy = paths["product_ops/deployment/deploy_cloud_run_waji.sh"].read_text(
        encoding="utf-8"
    )
    if '"--vpc-connector=${ODP_CLOUD_RUN_VPC_CONNECTOR}"' not in deploy:
        errors.append("deploy entrypoint does not pass the VPC connector to Cloud Run")
    if '"--vpc-egress=${ODP_CLOUD_RUN_VPC_EGRESS}"' not in deploy:
        errors.append("deploy entrypoint does not pass the VPC egress mode to Cloud Run")
    if "sources-off deploy requires ALL_TRAFFIC VPC egress" not in deploy:
        errors.append("deploy entrypoint does not fail closed on non-ALL_TRAFFIC sources-off egress")

    probe_wired = "public-egress-probe" in deploy
    receipt_wired = "public-egress-probe.json" in deploy
    if not probe_wired:
        errors.append("deploy entrypoint does not execute the public egress deny probe")
    if not receipt_wired:
        errors.append("deploy entrypoint does not write the public egress probe receipt")
    probe_call = "  run_public_egress_probe\n"
    if probe_call not in deploy:
        errors.append("deploy entrypoint does not call the public egress deny probe")
    if probe_wired and "promote_service_traffic" in deploy and probe_call in deploy:
        probe_pos = deploy.index(probe_call)
        promote_pos = deploy.index("promote_service_traffic")
        if probe_pos > promote_pos:
            errors.append("public egress deny probe must run before service traffic promotion")

    lifecycle = paths["product_ops/deployment/staging_lifecycle.py"].read_text(
        encoding="utf-8"
    )
    if "public_egress_denied_probe" not in lifecycle:
        errors.append("staging lifecycle does not retain the public egress deny probe stage")

    probe_entrypoint = paths["product_ops/deployment/cloud_run_job_entrypoint.py"].read_text(
        encoding="utf-8"
    )
    if "def run_public_egress_probe" not in probe_entrypoint:
        errors.append("Cloud Run Job entrypoint does not expose the public egress deny probe")

    cloud_run = paths["infra/terraform/cloud_run.tf"].read_text(encoding="utf-8")
    if 'egress = "ALL_TRAFFIC"' not in cloud_run:
        errors.append("cloud_run.tf does not enforce ALL_TRAFFIC VPC egress")

    network = paths["infra/terraform/network.tf"].read_text(encoding="utf-8")
    try:
        from infra.terraform.validate_contract import validate_egress_contract
    except ImportError as exc:  # pragma: no cover - repository packaging failure
        errors.append(f"cannot load Terraform egress contract verifier: {exc}")
    else:
        errors.extend(validate_egress_contract(network))
    return errors


def compute_migration_digest(root: Path = ROOT) -> str:
    """Compute deterministic SHA-256 digest over infra/db/migrations SQL files."""
    migrations_dir = root / "infra/db/migrations"
    return compute_file_set_digest(migrations_dir.glob("*.sql"), root=root)


def compute_data_contract_digest(root: Path = ROOT) -> str:
    """Compute deterministic SHA-256 digest over docs/data contract files."""
    data_dir = root / "docs/data"
    return compute_file_set_digest(data_dir.glob("*"), root=root)


def compute_source_policy_digest(root: Path = ROOT) -> str:
    """Compute deterministic SHA-256 digest over security/license policies."""
    policy_files = [
        root / "docs/security/license_policy.json",
        root / "docs/security/license_exemptions.json",
        root / "docs/security/release_bindings.json",
    ]
    return compute_file_set_digest(policy_files, root=root)


def build_release_manifest(
    *,
    release_id: str,
    candidate_sha: str,
    components: dict[str, dict[str, str]],
    sbom_refs: list[str],
    signature_refs: list[str],
    created_at: str,
    created_by_workflow: str,
    data_snapshot: dict[str, Any] | None = None,
    sources_off_attestation: dict[str, Any] | None = None,
    rollback_release: dict[str, Any] | None = None,
    external_sources_expected_enabled: list[str] | None = None,
    release_status: str | None = None,
    schema_version: int = CURRENT_SCHEMA_VERSION,
    root: Path = ROOT,
) -> dict[str, Any]:
    """Build and self-seal a canonical release manifest dictionary.

    ``release_status`` is omitted by default so an existing manifest digest is
    unchanged by this parameter's introduction.  A build phase that has already
    published signed images and an SBOM passes ``"ready"`` explicitly; nothing
    may promote a manifest to ``ready`` implicitly.
    """
    manifest: dict[str, Any] = {
        "schema_version": schema_version,
        "release_id": release_id,
        "candidate_sha": candidate_sha,
        "components": components,
        "migration_digest": compute_migration_digest(root=root),
        "data_contract_digest": compute_data_contract_digest(root=root),
        "source_policy_digest": compute_source_policy_digest(root=root),
        "external_sources_expected_enabled": external_sources_expected_enabled or [],
        "sbom_refs": sbom_refs,
        "signature_refs": signature_refs,
        "created_at": created_at,
        "created_by_workflow": created_by_workflow,
    }
    if data_snapshot is not None:
        manifest["data_snapshot"] = data_snapshot
    if sources_off_attestation is not None:
        manifest["sources_off_attestation"] = sources_off_attestation
    if rollback_release is not None:
        manifest["rollback_release"] = rollback_release
    if release_status is not None:
        manifest["release_status"] = release_status
    manifest["manifest_digest"] = compute_manifest_digest(manifest)
    return manifest


def extract_rollback_release_binding(prev_manifest: dict[str, Any]) -> dict[str, Any]:
    """Extract verifiable rollback binding from an approved previous manifest."""
    admission_errors = validate_release_admission(prev_manifest)
    if admission_errors:
        raise ValueError(
            "Cannot extract rollback binding from a non-admissible manifest: "
            + "; ".join(admission_errors)
        )
    components = prev_manifest.get("components", {})
    rb_components = {}
    for name, comp in components.items():
        if isinstance(comp, dict):
            item = {"image": comp.get("image", "")}
            if "shares_image_with" in comp:
                item["shares_image_with"] = comp["shares_image_with"]
            rb_components[name] = item
        else:
            rb_components[name] = {"image": str(comp)}

    prev_snapshot = (
        prev_manifest.get("data_snapshot")
        or prev_manifest.get("snapshot_pointer")
        or prev_manifest.get("snapshot")
    )
    binding: dict[str, Any] = {
        "release_id": prev_manifest["release_id"],
        "candidate_sha": prev_manifest["candidate_sha"],
        "manifest_digest": prev_manifest["manifest_digest"],
        "components": rb_components,
    }
    if isinstance(prev_snapshot, dict):
        binding["data_snapshot"] = copy.deepcopy(prev_snapshot)
    else:
        prev_attestation = prev_manifest.get("sources_off_attestation")
        if not isinstance(prev_attestation, dict):
            raise ValueError(
                "Cannot extract rollback binding from manifest without data_snapshot"
            )
        # Carry only the binding digest forward. The rollback pointer records
        # *which* posture the previous release was admitted on; it is not a
        # second place to re-state the posture itself.
        binding["sources_off_attestation"] = {
            "binding_digest": prev_attestation.get("binding_digest"),
        }

    return binding


def validate_rollback_manifest(
    prev_manifest: Any,
    *,
    current_candidate_sha: str | None = None,
    current_release_id: str | None = None,
) -> list[str]:
    """Validate an entire admissible previous manifest before extracting it."""
    if not isinstance(prev_manifest, dict):
        return ["rollback manifest must be a JSON object"]

    errors = validate_manifest(prev_manifest)
    errors.extend(
        err for err in validate_release_admission(prev_manifest) if err not in errors
    )

    candidate_sha = prev_manifest.get("candidate_sha")
    if current_candidate_sha and candidate_sha == current_candidate_sha:
        errors.append(
            "rollback candidate_sha must not match current candidate_sha; "
            "rollback target must be a distinct release candidate"
        )

    release_id = prev_manifest.get("release_id")
    if current_release_id and release_id == current_release_id:
        errors.append(
            "rollback release_id must not match current release_id; "
            "rollback target must be a distinct release"
        )

    snap = prev_manifest.get("data_snapshot")
    if not isinstance(snap, dict) or not snap:
        # A sources-off predecessor is legitimate rollback evidence, but only
        # through the same bound attestation admission required of it. A legacy
        # manifest that simply never had a snapshot is not.
        if not isinstance(prev_manifest.get("sources_off_attestation"), dict):
            errors.append(
                "rollback manifest missing required data_snapshot and "
                "sources_off_attestation; cannot use a legacy or evidence-less "
                "manifest as rollback evidence"
            )
    return errors


__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "EXPECTED_EXTERNAL_SOURCE_COUNT",
    "CREDENTIAL_ENV_SUFFIXES",
    "ENDPOINT_ENV_SUFFIXES",
    "STATUS_ENV_SUFFIXES",
    "GENERIC_SOURCE_ID_TOKENS",
    "classify_source_env_var",
    "env_var_belongs_to_source",
    "source_id_env_tokens",
    "EXTERNAL_SOURCE_INVENTORY",
    "RELEASE_ID_PATTERN",
    "RELEASE_STATUSES",
    "REQUIRED_FIELDS",
    "REQUIRED_FIELDS_V1",
    "REQUIRED_FIELDS_V2",
    "SNAPSHOT_FIELDS",
    "SOURCES_OFF_ATTESTATION_FIELDS",
    "SOURCES_OFF_EGRESS_CONTRACT_FILES",
    "SOURCES_OFF_EGRESS_EVIDENCE_FIELDS",
    "SOURCES_OFF_EGRESS_EVIDENCE_KIND",
    "SOURCES_OFF_EGRESS_POSTURE",
    "SOURCES_OFF_CLOUD_RUN_EGRESS",
    "SOURCES_OFF_FIREWALL_EGRESS",
    "SOURCES_OFF_PROVIDER_CREDENTIALS",
    "SOURCES_OFF_RUNTIME_PROBE",
    "SOURCES_OFF_RUNTIME_PROBE_RECEIPT",
    "SOURCES_OFF_RUNTIME_PROBE_RESULT",
    "SOURCES_OFF_RUNTIME_PROBE_REASON",
    "SOURCES_OFF_PROVIDER_MODE",
    "SOURCE_EGRESS_DENIED",
    "SOURCE_POSTURE_FIELDS",
    "SOURCE_STATUS_DISABLED",
    "ROOT",
    "SUPPORTED_SCHEMA_VERSIONS",
    "build_release_manifest",
    "build_sources_off_egress_evidence",
    "build_sources_off_attestation",
    "component_binding_errors",
    "compute_data_contract_digest",
    "compute_file_set_digest",
    "compute_manifest_digest",
    "compute_migration_digest",
    "compute_source_policy_digest",
    "compute_sources_off_egress_contract_digest",
    "compute_sources_off_probe_receipt_content_digest",
    "compute_sources_off_binding_digest",
    "extract_rollback_release_binding",
    "is_exact_sha",
    "is_sha256_digest",
    "load_manifest",
    "sources_off_attestation_errors",
    "sources_off_posture_payload",
    "validate_sources_off_probe_receipt",
    "validate_manifest",
    "validate_release_admission",
    "validate_rollback_manifest",
]


def _print_manifest_summary(manifest: dict[str, Any]) -> None:
    print(f"  Release ID:      {manifest['release_id']}")
    print(f"  Candidate SHA:   {manifest['candidate_sha']}")
    print(f"  Manifest digest: {manifest['manifest_digest']}")
    print(f"  Release status:  {manifest.get('release_status', 'ready')}")
    print(f"  Components:      {len(manifest.get('components', {}))}")
    if not manifest.get("components"):
        print("    - (none: no candidate image is bound to this manifest)")
    for name, comp in (manifest.get("components") or {}).items():
        print(f"    - {name}: {comp['image']}")
    if "data_snapshot" in manifest and isinstance(manifest["data_snapshot"], dict):
        snap = manifest["data_snapshot"]
        print(f"  Data Snapshot:   {snap.get('id')} ({snap.get('uri')}) [masked={snap.get('masked')}]")
    if "rollback_release" in manifest and isinstance(manifest["rollback_release"], dict):
        rb = manifest["rollback_release"]
        print(f"  Rollback Target: {rb.get('release_id')} ({rb.get('candidate_sha')})")


def main(argv: list[str] | None = None) -> int:
    """Validate a release manifest and refuse to bless a non-admissible one.

    The default mode answers the question an auditor actually has -- "may this
    manifest be deployed?" -- not merely "is this file well formed?".  A
    manifest that parses cleanly but records ``release_status='blocked'`` is a
    NO-GO, so it must exit non-zero and must never print a success verdict;
    otherwise this command becomes the same kind of fake green light the
    release gates exist to eliminate.  Pure structural checking is still
    available, but only when it is asked for explicitly.
    """

    import argparse

    parser = argparse.ArgumentParser(
        description="Validate an ODay Plus release manifest and its admission status.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=ROOT / "docs/evidence/gates/RELEASE_MANIFEST.json",
        help="Path to release manifest JSON",
    )
    parser.add_argument("--expected-sha", type=str, default=None, help="Expected candidate SHA")
    parser.add_argument("--expected-digest", type=str, default=None, help="Expected manifest digest")
    parser.add_argument(
        "--structure-only",
        action="store_true",
        help=(
            "Check schema and digest self-consistency only, without deciding "
            "release admission. Never reports a deployable verdict."
        ),
    )
    args = parser.parse_args(argv)

    manifest, errors = load_manifest(
        args.manifest,
        expected_candidate_sha=args.expected_sha,
        expected_digest=args.expected_digest,
    )
    if errors:
        print(f"INVALID: {len(errors)} structural error(s) in release manifest {args.manifest}:")
        for err in errors:
            print(f"  - {err}")
        return 1

    assert manifest is not None  # load_manifest reports an error when it is None

    if args.structure_only:
        print(f"STRUCTURE-OK: {args.manifest} is schema valid and digest self-consistent.")
        print("  Release admission was NOT evaluated (--structure-only).")
        _print_manifest_summary(manifest)
        return 0

    admission_errors = validate_release_admission(manifest)
    if admission_errors:
        print(f"BLOCKED: release manifest {args.manifest} is NOT admissible for deployment.")
        _print_manifest_summary(manifest)
        print(f"  Admission refused for {len(admission_errors)} reason(s):")
        for err in admission_errors:
            print(f"    - {err}")
        blockers = manifest.get("blockers")
        if isinstance(blockers, list) and blockers:
            print(f"  Recorded blockers ({len(blockers)}):")
            for blocker in blockers:
                if isinstance(blocker, dict):
                    print(
                        f"    - [{blocker.get('severity', '?')}] "
                        f"{blocker.get('id', '?')}: {blocker.get('reason', '')}"
                    )
                else:
                    print(f"    - {blocker}")
        return 1

    print(f"ADMISSIBLE: release manifest {args.manifest} may be deployed.")
    _print_manifest_summary(manifest)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())

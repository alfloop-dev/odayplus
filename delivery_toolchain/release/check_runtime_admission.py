#!/usr/bin/env python3
"""The single authoritative admission check for the Runtime Release workflow.

Nothing else may admit a deployment. Admission requires two things at once, and
neither is sufficient alone:

1. A signed, unexpired, unconsumed Supervisor release lease bound to this exact
   task, release, candidate SHA, manifest digest, target environment, and
   action. The lease is verified against an Ed25519 *public* key and consumed
   with a durable compare-and-set, so a workflow can check a lease but never
   mint one, and a captured lease cannot be replayed.
2. A staged gate registry that admits this environment: `release.decision` is
   `go`, `release.admission_target` is the requested environment, and every gate
   attached to that admission boundary is cleared with a passing receipt bound
   to the candidate SHA.

This replaces the previous shape-only check outright; there is no second
admission path. `task_id` and `release_lease` used to be regex-matched for
identifier characters and looked up against nothing, so any actor with workflow
write access could pass a lease they invented. That control did not exist, and
the module said so. It exists now, and the difference is that admission fails
closed when the Supervisor never issued the lease, already consumed it, revoked
it, or issued it for a different SHA, manifest, environment, or action.

Gate admission is staged, not cumulative. Requiring all seven gate categories
for every environment made staging evidence a prerequisite for creating staging
(EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN §6.1); admission is evaluated against
the gates bound to the requested `admission_target` instead.

A lease authorises the *deploy* phase only, and it is verified after the build
phase has already published the artifact it names. Requiring a lease before the
build made the manifest a precondition of producing the manifest, so the release
could never leave `blocked`; the Runtime Release workflow now runs
build -> admission -> deploy, and `--component-image` binds the image references
the deploy phase was handed back to `manifest.components`. Without that binding a
valid lease would admit any digest, and "build once, deploy that exact artifact"
would be a convention rather than a control.

Production deploys the staging-sealed manifest from its own Artifact Registry
(ODP-PROD-IMAGE-PROMOTION-001), so a production admission may also be handed
`--target-registry`: the production repository the deploy job promotes the
images into. Each handoff image is then either the manifest's own reference or
that reference with only the repository root replaced -- same image name, same
digest. Any other environment is refused that argument before the lease is
consumed.

Two responsibilities deliberately stay outside this check. GitHub environment
approval is the human production gate, and the workflow `concurrency` group is
same-environment mutual exclusion. A lease is not a substitute for either, and
neither is a substitute for a lease.

Operating it
------------

The Supervisor mints leases with `.orchestrator/release_lease.py issue` and
keeps the private key. This check needs two things the Supervisor does not have
to keep secret, and one it does not have to share at all:

* `ODP_RELEASE_LEASE_PUBLIC_KEY` (repository variable) - the Ed25519
  verification key. It cannot sign, so it is a variable rather than a secret.
* `--lease-state-dir` / `ODP_RELEASE_LEASE_STATE_URI` - the Supervisor's durable
  lease state. Hosted admission passes a `gs://bucket/prefix` URI, and the
  workflow rejects local paths before authentication. It must already exist and
  must be the same store the Supervisor wrote to at issuance. A hosted runner
  that cannot reach it cannot admit, which is the intended failure: consuming a
  lease in a directory that disappears with the runner does not stop a replay
  anywhere that matters.
* `--lineage-state-uri` / `ODP_PRODUCTION_RELEASE_LEASE_STATE_URI` - production's
  lease store, read only by a staging admission of an initial-release manifest
  (production's history is not in the staging store). Unset or unreadable, that
  admission is refused before the lease is consumed. Like the lease store, the
  workflow refuses it before authentication unless it is a `gs://bucket/prefix`.
* the private key, which stays with the Supervisor and is never needed here.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from delivery_toolchain.e2e.check_release_gate_registry import (  # noqa: E402
    CLEARED_STATUSES,
    STAGE_CONTRACT,
    SUPPORTED_SCHEMA_VERSIONS,
    check_candidate_ancestry,
)
from delivery_toolchain.release.release_lease import (  # noqa: E402
    DEFAULT_ACTION,
    SHA256_DIGEST_PATTERN,
    SHA_PATTERN,
    LeaseKeyError,
    LeaseStateError,
    LeaseStateStore,
    admit_and_consume,
    build_receipt,
    load_lease,
    load_public_key,
)
from delivery_toolchain.release.release_manifest import (  # noqa: E402
    IMAGE_PROMOTION_ENVIRONMENTS,
    INITIAL_RELEASE_LINEAGE_ENVIRONMENTS,
    INITIAL_RELEASE_LINEAGE_TARGET,
    component_binding_errors,
    initial_release_lineage_errors,
    initial_release_verification_errors,
    is_sha256_digest,
    load_manifest,
    manifest_release_profile,
    release_profile_errors,
    validate_release_admission,
)

DEFAULT_REGISTRY = ROOT / "docs/evidence/gates/RELEASE_GATE_REGISTRY.json"
DEFAULT_MANIFEST = ROOT / "docs/evidence/gates/RELEASE_MANIFEST.json"

# The unified Runtime Release workflow deploys dev, ephemeral staging, and
# production via single-path staged state machine.
DEPLOYABLE_ENVIRONMENTS = ("dev", "staging", "production")
PASSING_RECEIPT_RESULT = "pass"
NOT_APPLICABLE_STATUS = "not-applicable"
VERIFIER_NAME = "delivery_toolchain/release/check_runtime_admission.py"


def registry_admission_errors(
    registry: Any,
    *,
    release_sha: str,
    environment: str,
    expected_manifest_digest: str | None = None,
    allowed_environments: tuple[str, ...] = DEPLOYABLE_ENVIRONMENTS,
    root: Path = ROOT,
) -> list[str]:
    """Return why the staged gate registry does not admit *environment*.

    `allowed_environments` specifies the deployable environments admitted by
    the Runtime Release workflow (dev, staging, production).
    """

    errors: list[str] = []
    if environment not in allowed_environments:
        errors.append(f"environment must be one of {list(allowed_environments)}")
    if not SHA_PATTERN.fullmatch(release_sha):
        errors.append("release_sha must be an exact 40-character lowercase git SHA")
    if not isinstance(registry, dict):
        return errors + ["release gate registry must be a JSON object"]

    schema_version = registry.get("schema_version")
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        errors.append(
            f"registry.schema_version must be one of {list(SUPPORTED_SCHEMA_VERSIONS)}, "
            f"got: {schema_version!r}. A pre-staging registry has no admission boundary."
        )

    release = registry.get("release")
    if not isinstance(release, dict):
        return errors + ["registry.release is missing"]

    if release.get("decision") != "go":
        errors.append(f"registry decision is {release.get('decision')!r}, expected 'go'")

    errors.extend(_stage_boundary_errors(release, environment, allowed_environments))
    errors.extend(_manifest_binding_errors(release, expected_manifest_digest))

    candidate_sha = release.get("candidate_sha")
    if not isinstance(candidate_sha, str) or not SHA_PATTERN.fullmatch(candidate_sha):
        errors.append(f"registry candidate_sha {candidate_sha!r} is not a valid SHA")
        candidate_sha = None
    elif SHA_PATTERN.fullmatch(release_sha):
        errors.extend(check_candidate_ancestry(candidate_sha, release_sha, root))

    errors.extend(_gate_admission_errors(registry, environment, candidate_sha))
    return errors


def _stage_boundary_errors(
    release: dict[str, Any], environment: str, allowed_environments: tuple[str, ...]
) -> list[str]:
    errors: list[str] = []
    stage = release.get("stage")
    contract = STAGE_CONTRACT.get(stage) if isinstance(stage, str) else None
    if contract is None:
        errors.append(
            f"registry release.stage must be one of {list(STAGE_CONTRACT)}, got: {stage!r}"
        )
    admission_target = release.get("admission_target")
    if contract is not None and admission_target != contract[1]:
        errors.append(
            f"registry release.admission_target {admission_target!r} does not match "
            f"stage {stage!r} (expected {contract[1]!r})"
        )
    if environment in allowed_environments and admission_target != environment:
        errors.append(
            f"registry admits {admission_target!r}, not {environment!r}; the release has "
            "not reached the stage that admits this environment"
        )
    return errors


def _manifest_binding_errors(
    release: dict[str, Any], expected_manifest_digest: str | None
) -> list[str]:
    errors: list[str] = []
    digest = release.get("manifest_digest")
    if not isinstance(digest, str) or not SHA256_DIGEST_PATTERN.fullmatch(digest):
        errors.append("registry release.manifest_digest must be a sha256:<64 hex> digest")
        return errors
    if expected_manifest_digest and digest != expected_manifest_digest:
        errors.append(
            "registry release.manifest_digest does not match the manifest committed at "
            "the release SHA; the registry and the artifact identity disagree"
        )
    return errors


def _gate_admission_errors(
    registry: dict[str, Any], environment: str, candidate_sha: str | None
) -> list[str]:
    errors: list[str] = []
    gates = registry.get("gates")
    if not isinstance(gates, list) or len(gates) != 7:
        return ["registry must contain exactly seven gates"]

    admitting = []
    for index, gate in enumerate(gates):
        if not isinstance(gate, dict):
            errors.append(f"gates[{index}] is not an object")
            continue
        if gate.get("admission_target") == environment:
            admitting.append(gate)

    if errors:
        return errors
    if not admitting:
        return [
            f"no gate is bound to admission_target {environment!r}; the registry admits "
            "nothing for this environment"
        ]

    for gate in admitting:
        gate_id = str(gate.get("id") or "unknown-gate")
        status = gate.get("status")
        if status not in CLEARED_STATUSES:
            errors.append(f"{gate_id} status is {status!r}, which does not clear the gate")
        if candidate_sha and gate.get("release_sha") != candidate_sha:
            errors.append(f"{gate_id} release_sha does not match candidate_sha")
        if status == NOT_APPLICABLE_STATUS:
            continue
        errors.extend(_receipt_errors(gate, gate_id, candidate_sha))
    return errors


def _receipt_errors(
    gate: dict[str, Any], gate_id: str, candidate_sha: str | None
) -> list[str]:
    receipts = gate.get("receipts")
    if not isinstance(receipts, list) or not receipts:
        return [f"{gate_id} has no release receipt"]
    for receipt in receipts:
        if not isinstance(receipt, dict):
            continue
        if receipt.get("result") != PASSING_RECEIPT_RESULT:
            continue
        if candidate_sha and receipt.get("release_sha") != candidate_sha:
            continue
        return []
    return [
        f"{gate_id} has no passing receipt bound to candidate_sha; a receipt naming a "
        "different SHA is stale, not evidence"
    ]


def initial_release_admission_errors(
    manifest: Any,
    *,
    environment: str,
    receipt: Any,
    state_store: LeaseStateStore,
    now: datetime,
    lineage_state_store: LeaseStateStore | None = None,
    lineage_store_errors: list[str] | None = None,
) -> list[str]:
    """Return why a first-release manifest is not admissible *now*, *here*.

    A manifest that binds ``rollback_release`` is not asked anything. One that
    carries ``initial_release_recovery`` is admitted only with a fresh,
    digest-bound readback of this environment's target taken during this
    admission (ODP-CORE-OPS-FIRST-RELEASE-ROLLBACK-001), and -- for staging and
    production -- only while production's durable lease history shows it has
    never admitted another release. Every refusal here lands in the errors that
    block consumption, so the single-use lease survives it.

    Production's history lives in production's lease store. A production
    admission reads it as its own ``state_store``. A staging admission's own
    store holds only staging leases, so it must be handed production's store
    as ``lineage_state_store``; without it, or when it cannot be read, the
    staging admission is refused.
    """

    if not isinstance(manifest, dict) or manifest.get("initial_release_recovery") is None:
        return []
    errors = list(
        initial_release_verification_errors(
            receipt, manifest=manifest, environment=environment, now=now
        )
    )
    if environment not in INITIAL_RELEASE_LINEAGE_ENVIRONMENTS:
        return errors

    lineage_errors: list[str] = list(lineage_store_errors or [])
    records: Any = None
    cross_environment = environment != INITIAL_RELEASE_LINEAGE_TARGET
    if not cross_environment:
        store: LeaseStateStore | None = state_store
        if lineage_state_store is not None and str(lineage_state_store.directory) != str(
            state_store.directory
        ):
            lineage_errors.append(
                f"{INITIAL_RELEASE_LINEAGE_TARGET} admission reads its lineage from its "
                "own lease store; a different --lineage-state-uri is refused"
            )
    else:
        store = lineage_state_store
        if store is None and not lineage_errors:
            lineage_errors.append(
                f"{environment} initial-release admission needs "
                f"{INITIAL_RELEASE_LINEAGE_TARGET}'s lease store (--lineage-state-uri); "
                f"the {environment} store never records a "
                f"{INITIAL_RELEASE_LINEAGE_TARGET} admission, so it cannot prove "
                f"{INITIAL_RELEASE_LINEAGE_TARGET} has never admitted a release"
            )
        elif store is not None and str(store.directory) == str(state_store.directory):
            lineage_errors.append(
                f"--lineage-state-uri names the {environment} lease store itself; "
                f"{INITIAL_RELEASE_LINEAGE_TARGET}'s lineage has to be read from "
                f"{INITIAL_RELEASE_LINEAGE_TARGET}'s store"
            )
            store = None
    if store is not None and not lineage_errors:
        try:
            records = store.list_records()
        except LeaseStateError as exc:
            lineage_errors.append(
                f"cannot read {INITIAL_RELEASE_LINEAGE_TARGET} lease history: {exc}"
            )
    if lineage_errors:
        errors.extend(error for error in lineage_errors if error not in errors)
        return errors
    errors.extend(
        error
        for error in initial_release_lineage_errors(
            records,
            environment=environment,
            manifest_digest=manifest.get("manifest_digest"),
            lineage_target_store=cross_environment,
        )
        if error not in errors
    )
    return errors


def admit_release(
    registry: Any,
    lease: Any,
    *,
    release_sha: str,
    environment: str,
    task_id: str,
    public_key: Any,
    state_store: LeaseStateStore,
    manifest: Any = None,
    manifest_digest: str | None = None,
    manifest_errors: list[str] | None = None,
    component_images: dict[str, str] | None = None,
    target_registry: str | None = None,
    allowed_action: str = DEFAULT_ACTION,
    consumed_by: str,
    initial_release_receipt: Any = None,
    lineage_state_store: LeaseStateStore | None = None,
    lineage_store_errors: list[str] | None = None,
    root: Path = ROOT,
    now: datetime | None = None,
) -> tuple[bool, list[str], dict[str, Any]]:
    """Run the whole admission decision and consume the lease when it passes."""

    extra = list(manifest_errors or [])
    extra.extend(
        error
        for error in initial_release_admission_errors(
            manifest,
            environment=environment,
            receipt=initial_release_receipt,
            state_store=state_store,
            now=now or datetime.now(UTC),
            lineage_state_store=lineage_state_store,
            lineage_store_errors=lineage_store_errors,
        )
        if error not in extra
    )
    # The acceptance scope is sealed into the manifest digest; this is where it
    # is bound to the target. A dev-admin manifest fails here, before the lease
    # is consumed and before anything mutates the cloud, when it is pointed at
    # staging or production.
    extra.extend(
        error
        for error in release_profile_errors(manifest, environment=environment)
        if error not in extra
    )
    # dev and staging deploy the manifest's references as recorded; only an
    # environment that promotes the images first may name its own registry.
    if target_registry is not None and environment not in IMAGE_PROMOTION_ENVIRONMENTS:
        extra.append(
            f"--target-registry is accepted only for {list(IMAGE_PROMOTION_ENVIRONMENTS)} "
            f"admission, not {environment!r}; {environment} deploys the images the "
            "manifest records"
        )
        target_registry = None
    if component_images:
        # A manifest that failed to load is `None` here, and binding against it
        # reports an error rather than silently skipping the check.
        extra.extend(
            error
            for error in component_binding_errors(
                manifest, component_images, target_registry=target_registry
            )
            if error not in extra
        )
    extra.extend(
        registry_admission_errors(
            registry,
            release_sha=release_sha,
            environment=environment,
            expected_manifest_digest=manifest_digest,
            root=root,
        )
    )

    expected_candidate_sha = None
    if isinstance(registry, dict):
        candidate = (registry.get("release") or {}).get("candidate_sha")
        if isinstance(candidate, str) and SHA_PATTERN.fullmatch(candidate):
            expected_candidate_sha = candidate

    return admit_and_consume(
        lease,
        public_key=public_key,
        state_store=state_store,
        consumed_by=consumed_by,
        verifier=VERIFIER_NAME,
        extra_errors=extra,
        expected_task_id=task_id,
        expected_candidate_sha=expected_candidate_sha,
        expected_manifest_digest=manifest_digest,
        expected_environment=environment,
        expected_action=allowed_action,
        now=now,
    )


def _consumer_label(environment: str, release_sha: str) -> str:
    run = os.environ.get("GITHUB_RUN_ID")
    repository = os.environ.get("GITHUB_REPOSITORY")
    if run and repository:
        return f"github://{repository}/actions/runs/{run} ({environment} {release_sha})"
    return f"local-runtime-release ({environment} {release_sha})"


def _emit(receipt: dict[str, Any], receipt_path: Path | None) -> None:
    if receipt_path is None:
        return
    try:
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(
            json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    except OSError as exc:
        print(f"warning: cannot write admission receipt to {receipt_path}: {exc}", file=sys.stderr)


def _blocked(errors: list[str], receipt: dict[str, Any], receipt_path: Path | None) -> int:
    _emit(receipt, receipt_path)
    print("runtime admission blocked:", file=sys.stderr)
    for error in errors:
        print(f"- {error}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sha", required=True, dest="release_sha")
    parser.add_argument("--environment", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument(
        "--lease-file",
        required=True,
        type=Path,
        help="Signed Supervisor release lease document (JSON).",
    )
    parser.add_argument(
        "--lease-state-dir",
        required=True,
        type=str,
        help="Durable Supervisor lease state directory or gs://bucket/prefix. Must already exist.",
    )
    parser.add_argument(
        "--public-key-file",
        type=Path,
        default=None,
        help="Ed25519 verification key; defaults to the ODP_RELEASE_LEASE_PUBLIC_KEY env var.",
    )
    parser.add_argument(
        "--component-image",
        action="append",
        default=[],
        metavar="NAME=REF",
        help=(
            "Immutable image reference the deploy phase was handed for one manifest "
            "component, e.g. api=repo/api@sha256:... . Repeat per component; each one "
            "must equal the image recorded by manifest.components."
        ),
    )
    parser.add_argument(
        "--target-registry",
        default=None,
        metavar="REGION-docker.pkg.dev/PROJECT/REPOSITORY",
        help=(
            "Production only: the admission environment's own Artifact Registry "
            "repository the deploy job promotes the manifest images into. A handoff "
            "image may then also be the manifest image with only this repository "
            "root replaced (same image name and digest)."
        ),
    )
    parser.add_argument("--action", default=DEFAULT_ACTION)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--manifest-digest",
        default="",
        metavar="sha256:<64 hex>",
        help=(
            "Exact manifest digest this deploy is authorised for. The deploy phase "
            "transports the build phase's candidate manifest as an artifact, so the "
            "file handed here is not trusted on its own: it is admitted only when its "
            "canonical digest equals this value, which is the same digest the "
            "Supervisor lease was issued against."
        ),
    )
    parser.add_argument(
        "--require-manifest-digest",
        action="store_true",
        help=(
            "Refuse to admit without --manifest-digest. The hosted deploy phase always "
            "sets this: without it a transported manifest would be believed on the "
            "strength of being self-consistent, which any rebuilt manifest also is."
        ),
    )
    parser.add_argument(
        "--initial-release-receipt",
        type=Path,
        default=None,
        help=(
            "Admission-time target readback written by probe_release_target_absence.py "
            "--manifest. Required, fresh and bound to the manifest digest whenever the "
            "admitted manifest carries initial_release_recovery; ignored otherwise."
        ),
    )
    parser.add_argument(
        "--lineage-state-uri",
        default="",
        help=(
            "Production's durable lease store (gs://bucket/prefix or directory), read "
            "for a staging admission of an initial-release manifest so staging can "
            "prove production never admitted another release. Required then; a "
            "production admission reads its own --lease-state-dir and refuses a "
            "different value here. Hosted admission accepts only gs://bucket/prefix, "
            "as for --lease-state-dir."
        ),
    )
    parser.add_argument("--receipt", type=Path, default=None)
    args = parser.parse_args(argv)

    expected_manifest_digest = str(args.manifest_digest or "").strip()

    now = datetime.now(UTC)
    lease, lease_load_errors = load_lease(args.lease_file)

    def manifest_transport() -> dict[str, Any]:
        # Which manifest file was actually read, and which digest it had to
        # match. A receipt that omits this cannot distinguish an admission that
        # verified the build phase's transported artifact from one that fell
        # back to whatever manifest the deploy checkout carried.
        return {
            "manifest_path": str(args.manifest),
            "expected_manifest_digest": expected_manifest_digest or None,
            "digest_binding_required": bool(args.require_manifest_digest),
        }

    def fail(errors: list[str]) -> int:
        receipt = build_receipt(
            lease,
            errors=errors,
            admitted=False,
            verified_at=now,
            verifier=VERIFIER_NAME,
        )
        receipt["manifest_transport"] = manifest_transport()
        return _blocked(errors, receipt, args.receipt)

    if lease_load_errors:
        return fail(lease_load_errors)

    # The transported manifest is checked before anything reaches lease state.
    # A deploy that cannot say which manifest it is authorised for has no way to
    # distinguish the build phase's artifact from any other self-consistent one.
    if expected_manifest_digest and not is_sha256_digest(expected_manifest_digest):
        return fail(
            [
                "--manifest-digest must be a sha256:<64 lowercase hex> digest, got: "
                f"{expected_manifest_digest!r}"
            ]
        )
    if args.require_manifest_digest and not expected_manifest_digest:
        return fail(
            [
                "--manifest-digest is required: the deploy phase must name the exact "
                "manifest the Supervisor lease was issued against, not accept whichever "
                "manifest the checkout happens to carry"
            ]
        )

    try:
        public_key = load_public_key(key_path=args.public_key_file)
    except LeaseKeyError as exc:
        return fail([str(exc)])

    try:
        state_store = LeaseStateStore(args.lease_state_dir, require_existing=True)
    except LeaseStateError as exc:
        return fail([str(exc)])

    try:
        registry = json.loads(args.registry.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return fail([f"cannot read release gate registry: {exc}"])

    expected_candidate_sha = None
    if isinstance(registry, dict):
        candidate = (registry.get("release") or {}).get("candidate_sha")
        if isinstance(candidate, str) and SHA_PATTERN.fullmatch(candidate):
            expected_candidate_sha = candidate
    manifest, manifest_errors = load_manifest(
        args.manifest,
        expected_candidate_sha=expected_candidate_sha,
        expected_digest=expected_manifest_digest or None,
    )
    if manifest is not None:
        # Bound to the requested environment: an initial-release record admits
        # only the environments on its sealed target's admission path.
        admission_errors = validate_release_admission(
            manifest, environment=args.environment
        )
        manifest_errors.extend(
            error for error in admission_errors if error not in manifest_errors
        )
    manifest_digest = manifest.get("manifest_digest") if manifest and not manifest_errors else None

    component_images: dict[str, str] = {}
    for raw in args.component_image:
        name, _, ref = str(raw).partition("=")
        if not name.strip() or not ref.strip():
            return fail([f"--component-image expects NAME=REF, got: {raw!r}"])
        component_images[name.strip()] = ref.strip()

    # Opened only when it can matter, so an ordinary release never needs access
    # to another environment's store. Failure to open it is carried into the
    # admission errors, which refuse before the lease is consumed.
    lineage_state_store: LeaseStateStore | None = None
    lineage_store_errors: list[str] = []
    lineage_uri = str(args.lineage_state_uri or "").strip()
    if (
        lineage_uri
        and isinstance(manifest, dict)
        and manifest.get("initial_release_recovery") is not None
    ):
        try:
            lineage_state_store = LeaseStateStore(lineage_uri, require_existing=True)
        except LeaseStateError as exc:
            lineage_store_errors.append(
                f"cannot open {INITIAL_RELEASE_LINEAGE_TARGET} lease history: {exc}"
            )

    initial_release_receipt: Any = None
    if args.initial_release_receipt is not None:
        try:
            initial_release_receipt = json.loads(
                args.initial_release_receipt.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as exc:
            # An unreadable receipt is reported as what it is; the manifest
            # check below still refuses a first release that has none.
            manifest_errors.append(f"cannot read initial-release receipt: {exc}")

    admitted, errors, receipt = admit_release(
        registry,
        lease,
        release_sha=args.release_sha,
        environment=args.environment,
        task_id=args.task_id,
        public_key=public_key,
        state_store=state_store,
        manifest=manifest,
        manifest_digest=manifest_digest,
        manifest_errors=manifest_errors,
        component_images=component_images,
        target_registry=args.target_registry,
        allowed_action=args.action,
        consumed_by=_consumer_label(args.environment, args.release_sha),
        initial_release_receipt=initial_release_receipt,
        lineage_state_store=lineage_state_store,
        lineage_store_errors=lineage_store_errors,
        now=now,
    )
    # Digests are not secrets, and an admission receipt that does not say which
    # artifact was admitted cannot be audited against what actually deployed.
    receipt["component_images"] = dict(sorted(component_images.items()))
    receipt["target_registry"] = args.target_registry
    receipt["manifest_transport"] = manifest_transport()
    # The deploy phase reads the acceptance scope from here and nowhere else, so
    # it is only recorded for a manifest that verified against its digest.
    receipt["release_profile"] = (
        manifest_release_profile(manifest) if manifest_digest else None
    )
    if not admitted:
        return _blocked(errors, receipt, args.receipt)

    _emit(receipt, args.receipt)
    print(
        f"runtime admission passed: environment={args.environment} "
        f"sha={args.release_sha} task={args.task_id} lease={receipt['lease_id']} "
        f"(lease consumed at {receipt['consumed_at']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

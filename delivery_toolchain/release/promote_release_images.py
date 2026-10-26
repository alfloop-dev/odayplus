#!/usr/bin/env python3
"""把已 admission 的 manifest image 以相同 digest 複製到 production 自己的 registry。

ODP-PROD-IMAGE-PROMOTION-001。production 只收 staging 封存的 manifest，manifest
記錄的四個 component image 在 staging 專案的 Artifact Registry。2026-10-24 負責人
決定 production runtime 不得依賴 staging 專案，所以 production deploy job 在
admission 之後、任何 Cloud Run 變更之前跑這個步驟：

* 只接受 ``IMAGE_PROMOTION_ENVIRONMENTS``（production）。
* 讀 deploy job 自己下載的 manifest，digest 必須等於 admission 消耗 lease 時的
  ``manifest_digest``，candidate SHA 必須等於這次 release。
* admission 收到的 handoff image 要先以同一條規則（``component_binding_errors``
  帶 ``target_registry``）重新核對，才能拿來決定要複製什麼。
* 每個 component 以 ``cosign copy``（不加 ``--force``）從 manifest 記錄的
  ``repo@sha256:digest`` 複製到 ``promoted_image_ref``（同 image 名稱、同 digest，
  只換 repository 根），Cosign 簽章與 SBOM attestation 一起複製。tag 用 build 的
  ``release-<sha>``；production 已有同名 tag 但指向別的 digest 時拒絕，不移動它；
  已有內容相同的 artifact 時 cosign 略過，內容不同時 cosign 拒絕覆寫。
* 讀回：tag 與 digest reference 的 digest 都必須等於 manifest 的 digest；
  ``sha256-<hex>.sig``／``.att`` 讀回的 digest 必須是 manifest
  ``signature_refs``／``sbom_refs`` 記錄的那一份；再用既有的
  ``sign_images.sh verify`` 在 production registry 上驗簽。

任何一步失敗就以非零結束，不寫 GitHub output：deploy step 拿不到 production 的
reference，``deploy_cloud_run_waji.sh`` 也拒絕部署不在 production registry 的
image，Cloud Run 不會被變更。manifest 本身不改寫，lease 綁定的 digest 不變。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from delivery_toolchain.release.release_manifest import (  # noqa: E402
    IMAGE_PROMOTION_ENVIRONMENTS,
    SHA256_PATTERN,
    component_binding_errors,
    load_manifest,
    promoted_image_ref,
)

RECEIPT_KIND = "release-image-promotion"
SIGN_IMAGES = ROOT / "delivery_toolchain/security/sign_images.sh"


class PromotionError(Exception):
    """A promotion step failed; nothing downstream may deploy."""


def _component_image(component: Any) -> Any:
    return component.get("image") if isinstance(component, dict) else component


def resolve_digest(ref: str) -> str | None:
    """Read *ref*'s digest back the way the build phase does; ``None`` if unreadable."""

    proc = subprocess.run(
        [
            "gcloud", "artifacts", "docker", "images", "describe", ref,
            "--format=value(image_summary.digest)",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    digest = proc.stdout.strip()
    if proc.returncode != 0 or not SHA256_PATTERN.fullmatch(digest):
        return None
    return digest


def _run(command: list[str], what: str, env: dict[str, str] | None = None) -> None:
    proc = subprocess.run(command, check=False, env=env)
    if proc.returncode != 0:
        raise PromotionError(f"{what} failed (exit {proc.returncode})")


def _promoted_refs(refs: Any, registry: str) -> set[str]:
    if not isinstance(refs, list):
        return set()
    return {ref for ref in (promoted_image_ref(raw, registry) for raw in refs) if ref}


def promote(
    manifest: dict[str, Any],
    handoff: dict[str, str],
    *,
    release_sha: str,
    target_registry: str,
) -> dict[str, dict[str, str]]:
    """Copy, read back and verify every component; return the promoted record."""

    components = manifest.get("components")
    if not isinstance(components, dict):
        raise PromotionError("manifest.components must be an object")
    # A component that shares another's image (migration runs the worker image)
    # is deployed from, and promoted with, that component; it must not name an
    # image of its own that nothing would copy.
    deployable: set[str] = set()
    for name, component in components.items():
        shared = component.get("shares_image_with") if isinstance(component, dict) else None
        if shared is None:
            deployable.add(name)
        elif _component_image(component) != _component_image(components.get(shared)):
            raise PromotionError(
                f"manifest component {name!r} shares the image of {shared!r} but records "
                "a different one"
            )
    if set(handoff) != deployable:
        raise PromotionError(
            "the admitted handoff must name exactly the deployable manifest components "
            f"{sorted(deployable)}; got {sorted(handoff)}"
        )
    errors = component_binding_errors(manifest, handoff, target_registry=target_registry)
    if errors:
        raise PromotionError("; ".join(errors))

    plan: dict[str, tuple[str, str]] = {}
    for name in sorted(deployable):
        source = _component_image(components[name])
        promoted = promoted_image_ref(source, target_registry)
        if promoted is None:
            raise PromotionError(
                f"manifest image for {name!r} is not an Artifact Registry @sha256 "
                f"reference; it cannot be promoted into {target_registry}"
            )
        plan[name] = (source, promoted)

    signatures = _promoted_refs(manifest.get("signature_refs"), target_registry)
    attestations = _promoted_refs(manifest.get("sbom_refs"), target_registry)
    hosts = sorted({ref.split("/", 1)[0] for pair in plan.values() for ref in pair})
    _run(
        ["gcloud", "auth", "configure-docker", ",".join(hosts), "--quiet"],
        "gcloud auth configure-docker",
    )

    verify_env = dict(os.environ, CI="true")
    record: dict[str, dict[str, str]] = {}
    for name, (source, promoted) in plan.items():
        repository, digest = promoted.split("@", 1)
        tagged = f"{repository}:release-{release_sha}"
        if source != promoted:
            existing = resolve_digest(tagged)
            if existing is not None and existing != digest:
                raise PromotionError(
                    f"{tagged} already points at {existing}, not {digest}; refusing to "
                    "move an existing release tag"
                )
            # Without --force cosign skips what is already identical and refuses
            # to overwrite anything that differs (signatures included).
            _run(["cosign", "copy", source, tagged], f"cosign copy for {name}")
        for ref in (tagged, promoted):
            observed = resolve_digest(ref)
            if observed != digest:
                raise PromotionError(
                    f"{ref} reads back {observed or 'nothing'}, not the manifest digest "
                    f"{digest} for {name!r}"
                )
        hex_digest = digest.removeprefix("sha256:")
        supply_chain: dict[str, str] = {}
        for label, suffix, expected in (
            ("signature_ref", "sig", signatures),
            ("sbom_ref", "att", attestations),
        ):
            observed = resolve_digest(f"{repository}:sha256-{hex_digest}.{suffix}")
            ref = f"{repository}@{observed}" if observed else None
            if ref is None or ref not in expected:
                raise PromotionError(
                    f"{name!r} {suffix} artifact in {target_registry} reads back "
                    f"{ref or 'nothing'}, not one the manifest records"
                )
            supply_chain[label] = ref
        _run(
            [str(SIGN_IMAGES), "verify", promoted],
            f"signature verification of {promoted}",
            env=verify_env,
        )
        record[name] = {
            "source_image": source,
            "promoted_image": promoted,
            "digest": digest,
            **supply_chain,
            "signature_verified_by": "delivery_toolchain/security/sign_images.sh verify",
        }

    promoted_map = {name: entry["promoted_image"] for name, entry in record.items()}
    errors = component_binding_errors(manifest, promoted_map, target_registry=target_registry)
    if errors:
        raise PromotionError("; ".join(errors))
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--environment", required=True)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--expected-digest", required=True)
    parser.add_argument(
        "--target-registry",
        required=True,
        help="REGION-docker.pkg.dev/PROJECT/REPOSITORY of the admission environment.",
    )
    parser.add_argument("--component-image", action="append", default=[], metavar="NAME=REF")
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--github-output", type=Path, default=None)
    args = parser.parse_args(argv)

    receipt: dict[str, Any] = {
        "schema_version": 1,
        "kind": RECEIPT_KIND,
        "environment": args.environment,
        "release_sha": args.expected_sha,
        "manifest_digest": args.expected_digest,
        "target_registry": args.target_registry,
        "promoted": False,
        "components": {},
        "errors": [],
    }
    try:
        if args.environment not in IMAGE_PROMOTION_ENVIRONMENTS:
            raise PromotionError(
                f"image promotion is only for {list(IMAGE_PROMOTION_ENVIRONMENTS)}, "
                f"not {args.environment!r}"
            )
        handoff: dict[str, str] = {}
        for raw in args.component_image:
            name, _, ref = str(raw).partition("=")
            if not name.strip() or not ref.strip() or name.strip() in handoff:
                raise PromotionError(f"--component-image expects one NAME=REF each, got {raw!r}")
            handoff[name.strip()] = ref.strip()
        manifest, errors = load_manifest(
            args.manifest,
            expected_candidate_sha=args.expected_sha,
            expected_digest=args.expected_digest,
        )
        if manifest is None or errors:
            raise PromotionError("; ".join(errors) or "manifest could not be loaded")
        receipt["components"] = promote(
            manifest,
            handoff,
            release_sha=args.expected_sha,
            target_registry=args.target_registry,
        )
        receipt["promoted"] = True
    except PromotionError as exc:
        receipt["errors"] = [str(exc)]

    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if not receipt["promoted"]:
        print(f"Error: image promotion refused: {receipt['errors'][0]}", file=sys.stderr)
        return 1
    if args.github_output is not None:
        with args.github_output.open("a", encoding="utf-8") as handle:
            for name, entry in sorted(receipt["components"].items()):
                handle.write(f"{name}_image={entry['promoted_image']}\n")
    for name, entry in sorted(receipt["components"].items()):
        print(f"Promoted {name}: {entry['source_image']} -> {entry['promoted_image']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

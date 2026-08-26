#!/usr/bin/env python3
"""寫出 Runtime Release build 階段的 build-once artifact handoff。

build 階段是整條管線裡唯一允許產生 artifact 身分的地方。它輸出兩個檔案：

* ``runtime-release-images.json`` —— deploy 階段要帶回來的四個 immutable
  image reference。
* ``RELEASE_MANIFEST.json`` —— 候選 release manifest，記錄 component image
  digest、SBOM 參照、Cosign 簽章參照，以及綁定 exact release SHA 的
  migration / data contract / source policy digest。

這個 manifest 才是 Supervisor 簽發 lease 時要綁定的 ``manifest_digest``。
在 build 之前是無法知道它的——這正是舊管線「admission 先於 build」造成循環
依賴的原因。

可重跑性
--------

同一個 release SHA 重跑 build 階段必須得到**位元相同**的 handoff，否則已簽發
的 lease 會在重跑後失效。因此 manifest 內每一個欄位都只由「release SHA 所指向
的 tree」與「registry 中已存在的 immutable digest」決定，不含執行時間或 run id：

* ``release_id`` 預設為 ``odp-<release_sha[:12]>``。
* ``created_at`` 預設取 release SHA 的 committer date，而不是現在時間。
* ``created_by_workflow`` 指向 workflow 定義在該 SHA 的位置，而不是某一次 run。
  run id 屬於收據，不屬於不可變的 artifact 身分。

任何一項 image / SBOM / 簽章參照不是 immutable ``@sha256:`` reference 時，直接
拒絕寫檔：一份缺少供應鏈參照的 manifest 不是「比較弱的 manifest」，而是不能
放行的 manifest。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from delivery_toolchain.release.release_manifest import (  # noqa: E402
    IMAGE_DIGEST_PATTERN,
    build_release_manifest,
    is_exact_sha,
    validate_manifest,
    validate_release_admission,
)

# deploy 階段實際部署的四個 target。migration job 與 worker 共用同一個 image，
# 依 EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN §5.1 必須在 manifest 明確記錄
# 這個共用關係，因此 migration 會被展開成獨立的 component。
HANDOFF_COMPONENTS = ("api", "web", "worker", "scheduler")
SHARED_COMPONENTS = {"migration": "worker"}

DEFAULT_WORKFLOW_PATH = ".github/workflows/deploy-dev.yml"


class HandoffError(Exception):
    """handoff 無法被寫出的原因集合。"""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


def _parse_assignment(raw: str) -> tuple[str, str]:
    name, _, value = str(raw).partition("=")
    return name.strip(), value.strip()


def resolve_created_at(release_sha: str, root: Path = ROOT) -> str:
    """取 release SHA 的 committer date（正規化為 UTC）。

    用 commit 時間而不是現在時間，是為了讓同一個 SHA 的 handoff 可以重跑後
    位元相同。
    """

    proc = subprocess.run(
        ["git", "show", "-s", "--format=%cI", release_sha],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        raise HandoffError(
            [
                f"無法讀取 {release_sha} 的 commit 時間；"
                "請確認 build 階段 checkout 的就是這個 exact SHA。"
            ]
        )
    parsed = datetime.fromisoformat(proc.stdout.strip())
    return parsed.astimezone(UTC).replace(microsecond=0).isoformat()


def build_handoff(
    *,
    release_sha: str,
    components: dict[str, str],
    sbom_refs: list[str],
    signature_refs: list[str],
    release_id: str | None = None,
    created_at: str | None = None,
    created_by_workflow: str | None = None,
    repository: str = "alfloop-dev/odayplus",
    external_sources_expected_enabled: list[str] | None = None,
    root: Path = ROOT,
) -> tuple[dict[str, str], dict[str, Any]]:
    """回傳 ``(image handoff, release manifest)``，或在任何缺口時 raise。"""

    errors: list[str] = []

    if not is_exact_sha(release_sha):
        errors.append("release_sha 必須是 40 字元小寫 git SHA。")

    images: dict[str, str] = {}
    for name in HANDOFF_COMPONENTS:
        value = (components.get(name) or "").strip()
        if not value:
            errors.append(f"缺少 {name} 的 image reference；build 階段沒有產生完整 handoff。")
            continue
        if not IMAGE_DIGEST_PATTERN.fullmatch(value):
            errors.append(
                f"{name} 的 image reference 必須是 immutable @sha256 reference，"
                f"實際值為 {value!r}。"
            )
            continue
        images[name] = value

    unknown = sorted(set(components) - set(HANDOFF_COMPONENTS))
    if unknown:
        errors.append("handoff 含有非預期的 component（" + "、".join(unknown) + "）。")

    if not sbom_refs:
        errors.append(
            "缺少 SBOM 參照；沒有可取回的 SBOM artifact 就不能宣稱這個 release 有供應鏈證據。"
        )
    if not signature_refs:
        errors.append(
            "缺少 Cosign 簽章參照；沒有可驗證的簽章 artifact 就不能宣稱 image 已簽署。"
        )
    for label, refs in (("SBOM", sbom_refs), ("簽章", signature_refs)):
        for ref in refs:
            if not IMAGE_DIGEST_PATTERN.fullmatch(str(ref).strip()):
                errors.append(
                    f"{label}參照必須是 immutable @sha256 reference，實際值為 {ref!r}。"
                )

    if errors:
        raise HandoffError(errors)

    manifest_components = {name: {"image": images[name]} for name in HANDOFF_COMPONENTS}
    for shared, source in SHARED_COMPONENTS.items():
        manifest_components[shared] = {
            "image": images[source],
            "shares_image_with": source,
        }

    manifest = build_release_manifest(
        release_id=release_id or f"odp-{release_sha[:12]}",
        candidate_sha=release_sha,
        components=manifest_components,
        sbom_refs=sorted(dict.fromkeys(str(ref).strip() for ref in sbom_refs)),
        signature_refs=sorted(dict.fromkeys(str(ref).strip() for ref in signature_refs)),
        created_at=created_at or resolve_created_at(release_sha, root=root),
        created_by_workflow=(
            created_by_workflow
            or f"github://{repository}/{DEFAULT_WORKFLOW_PATH}@{release_sha}"
        ),
        external_sources_expected_enabled=external_sources_expected_enabled or [],
        release_status="ready",
        root=root,
    )

    # 自我驗證：不把一份自己都驗不過的 manifest 交給 admission。
    self_check = validate_manifest(manifest, expected_candidate_sha=release_sha)
    self_check.extend(
        error for error in validate_release_admission(manifest) if error not in self_check
    )
    if self_check:
        raise HandoffError(
            ["build 階段產出的 manifest 自我驗證失敗：" + error for error in self_check]
        )

    return images, manifest


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument(
        "--component",
        action="append",
        default=[],
        metavar="NAME=REF",
        help="Immutable component image reference, e.g. api=repo/api@sha256:...",
    )
    parser.add_argument("--sbom-ref", action="append", default=[])
    parser.add_argument("--signature-ref", action="append", default=[])
    parser.add_argument("--release-id", default=None)
    parser.add_argument("--created-at", default=None)
    parser.add_argument("--created-by-workflow", default=None)
    parser.add_argument("--repository", default="alfloop-dev/odayplus")
    parser.add_argument("--images-output", type=Path, required=True)
    parser.add_argument("--manifest-output", type=Path, required=True)
    parser.add_argument(
        "--github-output",
        type=Path,
        default=None,
        help="Optional GITHUB_OUTPUT file to receive manifest_digest and release_id.",
    )
    args = parser.parse_args(argv)

    components = dict(_parse_assignment(raw) for raw in args.component)
    try:
        images, manifest = build_handoff(
            release_sha=args.release_sha,
            components=components,
            sbom_refs=[ref for ref in args.sbom_ref if str(ref).strip()],
            signature_refs=[ref for ref in args.signature_ref if str(ref).strip()],
            release_id=args.release_id,
            created_at=args.created_at,
            created_by_workflow=args.created_by_workflow,
            repository=args.repository,
        )
    except HandoffError as exc:
        print("build-once artifact handoff 無法產生：", file=sys.stderr)
        for error in exc.errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    _write_json(args.images_output, images)
    _write_json(args.manifest_output, manifest)

    if args.github_output is not None:
        with args.github_output.open("a", encoding="utf-8") as handle:
            handle.write(f"manifest_digest={manifest['manifest_digest']}\n")
            handle.write(f"release_id={manifest['release_id']}\n")

    print(
        "build-once artifact handoff 已產生："
        f"release_id={manifest['release_id']} "
        f"candidate_sha={manifest['candidate_sha']} "
        f"manifest_digest={manifest['manifest_digest']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

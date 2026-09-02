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
import re
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
    compute_data_contract_digest,
    extract_rollback_release_binding,
    is_exact_sha,
    load_manifest,
    validate_manifest,
    validate_release_admission,
    validate_rollback_manifest,
)

# deploy 階段實際部署的四個 target。migration job 與 worker 共用同一個 image，
# 依 EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN §5.1 必須在 manifest 明確記錄
# 這個共用關係，因此 migration 會被展開成獨立的 component。
HANDOFF_COMPONENTS = ("api", "web", "worker", "scheduler")
SHARED_COMPONENTS = {"migration": "worker"}

DEFAULT_WORKFLOW_PATH = ".github/workflows/deploy-dev.yml"

# ``gs://bucket/key`` survives ``str`` but not ``Path``: pathlib collapses the
# double slash into ``gs:/bucket/key`` and the resulting "file does not exist"
# names a path nobody passed. A remote pointer is not a fetch instruction this
# script can honour -- the build phase reads the previous approved manifest out
# of its own workspace -- so it is rejected by name instead.
REMOTE_URI_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")


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
    data_snapshot: dict[str, Any] | None = None,
    rollback_manifest: dict[str, Any] | str | Path | None = None,
    rollback_release: dict[str, Any] | str | Path | None = None,
    release_id: str | None = None,
    created_at: str | None = None,
    created_by_workflow: str | None = None,
    repository: str = "alfloop-dev/odayplus",
    external_sources_expected_enabled: list[str] | None = None,
    schema_version: int = 2,
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

    effective_release_id = release_id or f"odp-{release_sha[:12]}"
    if rollback_manifest is not None and rollback_release is not None:
        errors.append(
            "rollback_manifest 與 rollback_release（CLI: --rollback-manifest 與 "
            "--rollback-release-file）不可同時指定；兩者都必須指向同一份完整 "
            "previous release manifest，這裡不會替你選一條"
        )

    rollback_source = rollback_manifest if rollback_manifest is not None else rollback_release
    resolved_rollback_release = None
    if rollback_source is not None:
        previous_manifest: dict[str, Any] | None = None
        if isinstance(rollback_source, (str, Path)):
            raw_str = str(rollback_source).strip()
            if raw_str.startswith("{") and raw_str.endswith("}"):
                try:
                    previous_manifest = json.loads(raw_str)
                    previous_errors = validate_manifest(previous_manifest)
                except Exception as exc:
                    previous_manifest = None
                    previous_errors = [f"無法解析 rollback manifest JSON 字串：{exc}"]
            elif REMOTE_URI_PATTERN.match(raw_str):
                previous_manifest = None
                previous_errors = [
                    f"{raw_str} 是遠端 URI，不是本機路徑；rollback manifest 只接受 build "
                    "workspace 內的檔案路徑或 inline JSON。請先把上一核准 release manifest "
                    "取回工作區再傳入。"
                ]
            else:
                previous_manifest, previous_errors = load_manifest(Path(rollback_source))
            if previous_errors or previous_manifest is None:
                errors.extend([f"無法載入 rollback manifest：{e}" for e in previous_errors])
        elif isinstance(rollback_source, dict):
            previous_manifest = rollback_source
        else:
            errors.append("rollback manifest 必須是完整 manifest dict 或檔案路徑")

        if previous_manifest is not None:
            rb_errs = validate_rollback_manifest(
                previous_manifest,
                current_candidate_sha=release_sha,
                current_release_id=effective_release_id,
            )
            if rb_errs:
                errors.extend([f"rollback manifest 無效：{e}" for e in rb_errs])
            else:
                resolved_rollback_release = extract_rollback_release_binding(previous_manifest)

    if schema_version >= 2:
        if external_sources_expected_enabled and data_snapshot is None:
            errors.append(
                "缺少 masked data snapshot 參照；build 階段啟用外部資料來源時必須綁定本次核准的 masked snapshot。"
            )
        if resolved_rollback_release is None:
            errors.append(
                "缺少 rollback release 參照；build 階段必須綁定上一核准 release 與 snapshot pointer。"
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
        release_id=effective_release_id,
        candidate_sha=release_sha,
        components=manifest_components,
        sbom_refs=sorted(dict.fromkeys(str(ref).strip() for ref in sbom_refs)),
        signature_refs=sorted(dict.fromkeys(str(ref).strip() for ref in signature_refs)),
        created_at=created_at or resolve_created_at(release_sha, root=root),
        created_by_workflow=(
            created_by_workflow
            or f"github://{repository}/{DEFAULT_WORKFLOW_PATH}@{release_sha}"
        ),
        data_snapshot=data_snapshot,
        rollback_release=resolved_rollback_release,
        external_sources_expected_enabled=external_sources_expected_enabled or [],
        release_status="ready",
        schema_version=schema_version,
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
    parser.add_argument("--data-snapshot-id", default=None)
    parser.add_argument("--data-snapshot-uri", default=None)
    parser.add_argument("--data-snapshot-sha256", default=None)
    parser.add_argument("--data-snapshot-content-sha256", default=None)
    parser.add_argument("--data-snapshot-contract-digest", default=None)
    parser.add_argument("--data-snapshot-file", type=Path, default=None)
    parser.add_argument("--data-snapshot-unmasked", action="store_true", default=False)
    parser.add_argument(
        "--rollback-manifest",
        default=None,
        help=(
            "Previous approved release manifest, as a path inside the build "
            "workspace or as an inline JSON object"
        ),
    )
    parser.add_argument("--rollback-release-file", type=Path, default=None)
    parser.add_argument("--images-output", type=Path, required=True)
    parser.add_argument("--manifest-output", type=Path, required=True)
    parser.add_argument(
        "--github-output",
        type=Path,
        default=None,
        help="Optional GITHUB_OUTPUT file to receive manifest_digest and release_id.",
    )
    parser.add_argument(
        "--external-source",
        action="append",
        default=[],
        dest="external_sources_expected_enabled",
        help="External source expected to be enabled (default: none, sources-off).",
    )
    args = parser.parse_args(argv)

    components = dict(_parse_assignment(raw) for raw in args.component)

    # The approved snapshot arrives either as a whole file or as its separate
    # fields, and the workflow reads each channel from a different place
    # (dispatch inputs vs repository vars). Letting one channel win silently
    # means a stale `vars` entry can replace the snapshot an operator just
    # approved, and the manifest would record the substitution as if it were
    # the approval. Mixing the channels is refused, exactly as
    # ``rollback_manifest``/``rollback_release`` already are.
    inline_snapshot_fields = {
        "--data-snapshot-id": args.data_snapshot_id,
        "--data-snapshot-uri": args.data_snapshot_uri,
        "--data-snapshot-sha256": args.data_snapshot_sha256,
        "--data-snapshot-content-sha256": args.data_snapshot_content_sha256,
    }
    # These two never build a snapshot on their own, but they do modify the
    # inline one, so passing them alongside a file is the same ambiguity.
    inline_snapshot_modifiers = {
        "--data-snapshot-contract-digest": args.data_snapshot_contract_digest,
        "--data-snapshot-unmasked": args.data_snapshot_unmasked or None,
    }
    supplied_inline = sorted(
        name
        for name, value in {**inline_snapshot_fields, **inline_snapshot_modifiers}.items()
        if value
    )

    data_snapshot = None
    if args.data_snapshot_file and supplied_inline:
        print(
            "approved masked data snapshot 有兩條互斥的來源，這裡不會替你選一條：",
            file=sys.stderr,
        )
        print(
            f"- --data-snapshot-file {args.data_snapshot_file} 與 "
            + "、".join(supplied_inline)
            + " 同時指定。",
            file=sys.stderr,
        )
        print(
            "- 只留下實際核准的那一條；workflow 端請清掉未使用的 dispatch input "
            "或 repository vars fallback。",
            file=sys.stderr,
        )
        return 1

    if args.data_snapshot_file:
        try:
            data_snapshot = json.loads(args.data_snapshot_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"無法讀取 data snapshot 檔案 {args.data_snapshot_file}：{exc}", file=sys.stderr)
            return 1
    elif any(inline_snapshot_fields.values()):
        contract_digest = (
            args.data_snapshot_contract_digest
            or compute_data_contract_digest(root=ROOT)
        )
        raw_content_sha = (
            args.data_snapshot_content_sha256
            or args.data_snapshot_sha256
            or ""
        ).strip()
        if len(raw_content_sha) == 64 and re.fullmatch(r"[0-9a-f]{64}", raw_content_sha):
            raw_content_sha = f"sha256:{raw_content_sha}"
        data_snapshot = {
            "id": (args.data_snapshot_id or "").strip(),
            "uri": (args.data_snapshot_uri or "").strip(),
            "content_sha256": raw_content_sha,
            "data_contract_digest": contract_digest,
            "masked": not args.data_snapshot_unmasked,
        }

    try:
        images, manifest = build_handoff(
            release_sha=args.release_sha,
            components=components,
            sbom_refs=[ref for ref in args.sbom_ref if str(ref).strip()],
            signature_refs=[ref for ref in args.signature_ref if str(ref).strip()],
            data_snapshot=data_snapshot,
            # Both, not `a or b`: collapsing them here would hide the same
            # substitution that build_handoff refuses for the snapshot.
            rollback_manifest=args.rollback_manifest,
            rollback_release=args.rollback_release_file,
            release_id=args.release_id,
            created_at=args.created_at,
            created_by_workflow=args.created_by_workflow,
            repository=args.repository,
            external_sources_expected_enabled=args.external_sources_expected_enabled,
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

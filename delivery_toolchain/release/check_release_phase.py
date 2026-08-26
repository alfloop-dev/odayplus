#!/usr/bin/env python3
"""Runtime Release 的階段前置檢查：build 與 deploy 各自的入場條件。

為什麼需要這一關
----------------

在這一關存在之前，`Runtime Release` 把 `admission`（簽章 lease 驗證）排在
`build` 之前。lease 由 Supervisor 綁定 `manifest_digest` 簽發，而 manifest 的
`components[*].image`、`sbom_refs`、`signature_refs` 只能由 build 產出——於是
「要 build 必須先有 lease，要有 lease 必須先 build」形成循環依賴，
`docs/evidence/gates/RELEASE_MANIFEST.json` 也因此長期停在 `blocked`。

修正方式是把單一管線切成兩個明確階段，而不是新增第二套 workflow：

* ``build``：不需要、也不接受 lease。它只需要 exact release SHA 與 OIDC/WIF，
  產出 immutable image digest、SBOM 與 Cosign 簽章，並寫出 build-once artifact
  handoff（image handoff + candidate release manifest）。
* ``deploy``：不再 build。它必須帶入 build 階段產出的四個 immutable image
  reference，以及一張只授權 ``deploy`` 動作的簽章 Supervisor lease。

這個模組是兩個階段共用的 fail-closed 前置檢查。缺少 artifact、缺少 lease、
缺少 OIDC/WIF 時一律拒絕，並輸出中文收據；收據只記錄 lease 是否存在，永遠
不記錄 lease 內容本身。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PHASES = ("build", "deploy")

# `deploy_cloud_run_waji.sh` 以 deploy-by-digest 部署這四個 target；migration job
# 與 worker 共用同一個 image，所以 handoff 只需要四個 reference。
HANDOFF_COMPONENTS = ("api", "web", "worker", "scheduler")

IMAGE_REF_PATTERN = re.compile(r"^[^\s]+@sha256:[0-9a-f]{64}$")
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")

VERIFIER_NAME = "delivery_toolchain/release/check_release_phase.py"
RECEIPT_KIND = "runtime-release-phase-precheck"


def phase_errors(
    *,
    phase: str,
    release_sha: str,
    environment: str,
    images: dict[str, str],
    lease_supplied: bool,
    oidc_configured: bool,
) -> list[str]:
    """回傳所有阻擋這個階段開始執行的理由（中文）；空 list 代表通過。"""

    errors: list[str] = []

    if phase not in PHASES:
        errors.append(f"phase 必須是 {list(PHASES)} 其中之一，實際值為 {phase!r}。")

    if not SHA_PATTERN.fullmatch(release_sha or ""):
        errors.append("release_sha 必須是 40 字元小寫 git SHA；不接受分支名稱或縮寫。")

    if not environment.strip():
        errors.append("environment 不得為空；部署目標必須明確。")

    if not oidc_configured:
        errors.append(
            "缺少 OIDC/WIF 設定（GCP_WORKLOAD_IDENTITY_PROVIDER 與 GCP_SERVICE_ACCOUNT）；"
            "沒有聯合身分就沒有可稽核的雲端身分，一律 fail closed。"
        )

    supplied = {name: value for name, value in images.items() if value.strip()}

    if phase == "build":
        if supplied:
            errors.append(
                "build 階段不得預先指定 image handoff（"
                + "、".join(sorted(supplied))
                + "）；build 階段的職責是產生 handoff，不是消費 handoff。"
            )
        if lease_supplied:
            errors.append(
                "build 階段不得帶入 Supervisor lease；lease 只授權 deploy 階段，"
                "在 build 階段接受 lease 會讓循環依賴重新出現。"
            )
    elif phase == "deploy":
        missing = [name for name in HANDOFF_COMPONENTS if not images.get(name, "").strip()]
        if missing:
            errors.append(
                "deploy 階段缺少 build-once artifact handoff："
                + "、".join(missing)
                + "；deploy 階段不得重新 build，缺少 artifact 時必須 fail closed。"
            )
        for name in HANDOFF_COMPONENTS:
            value = images.get(name, "").strip()
            if value and not IMAGE_REF_PATTERN.fullmatch(value):
                errors.append(
                    f"{name} 的 image handoff 必須是 immutable @sha256 reference，"
                    f"實際值為 {value!r}；可變 tag 無法證明部署的是同一個 artifact。"
                )
        unknown = sorted(set(supplied) - set(HANDOFF_COMPONENTS))
        if unknown:
            errors.append(
                "handoff 含有非預期的 component（" + "、".join(unknown) + "）。"
            )
        if not lease_supplied:
            errors.append(
                "deploy 階段缺少簽章 Supervisor lease；沒有 lease 就沒有部署授權，"
                "一律 fail closed。"
            )

    return errors


def build_receipt(
    *,
    phase: str,
    environment: str,
    release_sha: str,
    task_id: str,
    images: dict[str, str],
    lease_supplied: bool,
    oidc_configured: bool,
    errors: list[str],
    checked_at: datetime,
) -> dict[str, Any]:
    """組出中文 fail-closed 收據。收據永遠不含 lease 內容或任何 secret 值。"""

    admitted = not errors
    if admitted:
        summary = (
            f"{phase} 階段前置條件全部通過；release_sha={release_sha}、"
            f"environment={environment}。"
        )
    else:
        summary = (
            f"{phase} 階段被拒絕，共 {len(errors)} 項阻擋原因；"
            "此階段不得繼續執行。"
        )
    return {
        "receipt_kind": RECEIPT_KIND,
        "schema_version": 1,
        "verifier": VERIFIER_NAME,
        "phase": phase,
        "environment": environment,
        "release_sha": release_sha,
        "task_id": task_id,
        "admitted": admitted,
        "checked_at": checked_at.astimezone(UTC).replace(microsecond=0).isoformat(),
        # lease 只記錄「有沒有帶」。內容、簽章與 nonce 都不進收據。
        "lease_supplied": lease_supplied,
        "oidc_configured": oidc_configured,
        "image_handoff": {
            name: (images.get(name, "").strip() or None) for name in HANDOFF_COMPONENTS
        },
        "blockers_zh_tw": list(errors),
        "summary_zh_tw": summary,
        "secret_values_redacted": True,
    }


def _boolean(value: str) -> bool:
    return str(value).strip().lower() == "true"


def _write_receipt(receipt: dict[str, Any], receipt_path: Path | None) -> None:
    if receipt_path is None:
        return
    try:
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(
            json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    except OSError as exc:
        print(f"warning: 無法寫入階段收據 {receipt_path}: {exc}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", required=True)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--task-id", default="")
    for name in HANDOFF_COMPONENTS:
        parser.add_argument(f"--{name}-image", default="")
    # lease 只以「有沒有帶」的形式傳入，避免把簽章文件放進 argv。
    parser.add_argument("--lease-supplied", default="false")
    parser.add_argument("--oidc-configured", default="false")
    parser.add_argument("--receipt", type=Path, default=None)
    args = parser.parse_args(argv)

    images = {name: getattr(args, f"{name}_image") or "" for name in HANDOFF_COMPONENTS}
    lease_supplied = _boolean(args.lease_supplied)
    oidc_configured = _boolean(args.oidc_configured)

    errors = phase_errors(
        phase=args.phase,
        release_sha=args.release_sha,
        environment=args.environment,
        images=images,
        lease_supplied=lease_supplied,
        oidc_configured=oidc_configured,
    )
    receipt = build_receipt(
        phase=args.phase,
        environment=args.environment,
        release_sha=args.release_sha,
        task_id=args.task_id,
        images=images,
        lease_supplied=lease_supplied,
        oidc_configured=oidc_configured,
        errors=errors,
        checked_at=datetime.now(UTC),
    )
    _write_receipt(receipt, args.receipt)

    if errors:
        print(f"Runtime Release {args.phase} 階段前置檢查未通過：", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print(receipt["summary_zh_tw"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

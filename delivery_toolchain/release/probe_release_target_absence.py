#!/usr/bin/env python3
"""讀回部署 target，證明它沒有任何既有已核准 release。

schema v2 要求每個 release 綁定上一個已核准 release，讓「要回滾到哪一版」永遠
不是事故當下才要找的答案。這個要求沒有例外，而在**第一次**部署到某個 target 時
它無法被滿足：沒有上一版可綁，build 階段就永遠寫不出 handoff，環境也永遠收不到
第一次部署。

``initial_release_recovery`` 是唯一一條解開這個死結的分支，而它之所以可被採信，
是因為它綁定的是**讀回來的事實**而不是宣告。這個腳本就是那個讀回動作：

* ``--output``（build 階段）：逐一讀回這個 release 會部署的每個 Cloud Run
  service 與 job，全部不存在才寫出 readback receipt；任何一個存在就直接拒絕，
  不寫檔，並要求改走既有的 rollback 綁定。
* ``--manifest``（admission 階段）：拿已放行的 manifest 重讀一次 target。build
  當下的讀回不能代表 deploy 當下仍然成立，也不能代表那份 readback 真的來自這個
  target，所以 lease 被消費之前會在這裡重驗一次。

`gcloud` 失敗一律視為「無法斷定」而不是「不存在」。查不到跟查不動是兩件事，把
後者讀成前者，正好會在權限壞掉時放行一個假的 initial release。

readback 內容刻意不含時間或 run id：manifest_digest 綁著它，同一個 release SHA
重跑 build 必須逐位元重現同一份 handoff。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from delivery_toolchain.release.release_manifest import (  # noqa: E402
    INITIAL_RELEASE_ELIGIBLE_ENVIRONMENTS,
    INITIAL_RELEASE_PROBE_COMMAND,
    INITIAL_RELEASE_READBACK_KIND,
    INITIAL_RELEASE_TARGET_INVENTORY,
    initial_release_readback_payload,
    initial_release_recovery_errors,
    load_manifest,
    validate_release_admission,
)

VERIFICATION_KIND = "initial-release-recovery-verification"

#: ``gcloud run services list`` 與 ``gcloud run jobs list`` 讀的是不同的資源
#: 集合，這裡把 component 對應到它該問的那一個。
RESOURCE_COMMANDS = {
    "cloud-run-service": ("run", "services"),
    "cloud-run-job": ("run", "jobs"),
}


class ProbeError(Exception):
    """target 無法被讀回、或讀回結果不允許 initial-release 分支。"""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


def _parse_target(raw: str) -> tuple[str, str]:
    component, _, resource_name = str(raw).partition("=")
    return component.strip(), resource_name.strip()


def _lookup(
    *,
    gcloud: str,
    resource_kind: str,
    resource_name: str,
    project: str,
    region: str,
) -> bool:
    """回傳 *resource_name* 是否存在於 target。

    只用 ``list --filter``，因為 ``describe`` 的「找不到」與「沒權限」都是非零
    exit code，無法區分。``list`` 成功但輸出為空才是「不存在」這個事實；只要
    ``gcloud`` 自己失敗，就 raise 而不是把它讀成不存在。
    """

    group, noun = RESOURCE_COMMANDS[resource_kind]
    command = [
        gcloud,
        group,
        noun,
        "list",
        f"--region={region}",
        f"--project={project}",
        f"--filter=metadata.name={resource_name}",
        "--format=value(metadata.name)",
    ]
    proc = subprocess.run(command, check=False, capture_output=True, text=True)
    if proc.returncode != 0:
        raise ProbeError(
            [
                f"無法讀回 {resource_kind} {resource_name!r}："
                f"{' '.join(command)} 以 exit code {proc.returncode} 結束"
                f"（{proc.stderr.strip() or '無錯誤輸出'}）。"
                "讀不到不等於不存在，因此不放行 initial-release recovery。"
            ]
        )
    observed = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    if not observed:
        return False
    if observed != [resource_name]:
        raise ProbeError(
            [
                f"{resource_kind} {resource_name!r} 的查詢結果不唯一："
                f"{'、'.join(observed)}；無法斷定 target 是否為空。"
            ]
        )
    return True


def probe_target_absence(
    *,
    target_environment: str,
    project: str,
    region: str,
    targets: dict[str, str],
    gcloud: str = "gcloud",
) -> dict[str, Any]:
    """讀回整個部署 target，全空才回傳 readback；任何一個存在就 raise。"""

    errors: list[str] = []
    if target_environment not in INITIAL_RELEASE_ELIGIBLE_ENVIRONMENTS:
        errors.append(
            f"initial-release recovery 只適用於 "
            f"{list(INITIAL_RELEASE_ELIGIBLE_ENVIRONMENTS)}，"
            f"不是 {target_environment!r}；staging 與 production 維持既有的 "
            "rollback 綁定要求。"
        )
    for field, value in (("project", project), ("region", region)):
        if not str(value).strip():
            errors.append(f"缺少 {field}；沒有 target 座標就沒有可稽核的讀回。")

    expected = dict(INITIAL_RELEASE_TARGET_INVENTORY)
    missing = sorted(set(expected) - set(targets))
    unexpected = sorted(set(targets) - set(expected))
    if missing:
        errors.append(
            "缺少部署 target 的資源名稱（" + "、".join(missing) + "）；"
            "只讀回一部分 target，證明不了整個環境是空的。"
        )
    if unexpected:
        errors.append(
            "指定了非部署 target 的資源（" + "、".join(unexpected) + "）。"
        )
    for component, resource_name in sorted(targets.items()):
        if component in expected and not resource_name:
            errors.append(f"{component} 的資源名稱是空的；無法讀回。")
    if errors:
        raise ProbeError(errors)

    entries: list[dict[str, Any]] = []
    present: list[str] = []
    for component, resource_kind in sorted(INITIAL_RELEASE_TARGET_INVENTORY):
        resource_name = targets[component]
        exists = _lookup(
            gcloud=gcloud,
            resource_kind=resource_kind,
            resource_name=resource_name,
            project=project,
            region=region,
        )
        if exists:
            present.append(f"{component}（{resource_kind} {resource_name}）")
        entries.append(
            {
                "component": component,
                "resource_kind": resource_kind,
                "resource_name": resource_name,
                "exists": exists,
                # target 上不存在的資源不可能在送流量。這兩件事分開記錄，是因為
                # 「什麼都沒有」與「什麼都沒在服務」是兩個不同的主張，
                # initial-release admission 兩個都需要。
                "serving_traffic": exists,
            }
        )

    if present:
        raise ProbeError(
            [
                "target " + target_environment + " 已經有既有資源（"
                + "、".join(present)
                + "）；這不是首次 release。請改綁上一個已核准 release manifest，"
                "走既有的 rollback 路徑。"
            ]
        )

    return {
        "kind": INITIAL_RELEASE_READBACK_KIND,
        "target_environment": target_environment,
        "project": project,
        "region": region,
        "probe_command": INITIAL_RELEASE_PROBE_COMMAND,
        "targets": entries,
    }


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def verify_manifest_recovery(
    manifest: dict[str, Any],
    *,
    environment: str,
    project: str,
    region: str,
    targets: dict[str, str],
    gcloud: str = "gcloud",
) -> dict[str, Any]:
    """在 deploy 前重驗 initial-release admission，回傳可稽核 receipt。

    build 當下讀到的空 target 不代表 deploy 當下仍然是空的，也不代表那份
    readback 真的來自這個 target。重讀一次是這條分支唯一無法被偽造的部分，所以
    它排在 lease 被消費之前。
    """

    recovery = manifest.get("initial_release_recovery")
    if recovery is None:
        raise ProbeError(
            [
                "manifest 沒有 initial_release_recovery；這個檢查只適用於首次 "
                "release 的 admission。"
            ]
        )

    errors = initial_release_recovery_errors(
        recovery,
        candidate_sha=manifest.get("candidate_sha"),
        components=manifest.get("components"),
        environment=environment,
    )
    errors.extend(
        error
        for error in validate_release_admission(manifest, environment=environment)
        if error not in errors
    )
    if errors:
        raise ProbeError(errors)

    fresh = probe_target_absence(
        target_environment=environment,
        project=project,
        region=region,
        targets=targets,
        gcloud=gcloud,
    )
    recorded_payload = initial_release_readback_payload(recovery.get("absence_readback"))
    if initial_release_readback_payload(fresh) != recorded_payload:
        raise ProbeError(
            [
                "deploy 前重讀的 target readback 與 manifest 記錄的不一致；"
                "被放行的 readback 必須就是這個 target 現在的狀態。"
            ]
        )

    return {
        "schema_version": 1,
        "kind": VERIFICATION_KIND,
        "release_id": manifest.get("release_id"),
        "candidate_sha": manifest.get("candidate_sha"),
        "manifest_digest": manifest.get("manifest_digest"),
        "target_environment": recovery.get("target_environment"),
        "recovery_method": recovery.get("recovery_method"),
        "recovery_actions": list(recovery.get("recovery_actions") or []),
        "rollback_target_available": recovery.get("rollback_target_available"),
        "binding_digest": recovery.get("binding_digest"),
        "verified_absent": True,
        "absence_readback": fresh,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument(
        "--target",
        action="append",
        default=[],
        metavar="COMPONENT=RESOURCE_NAME",
        help=(
            "Deploy target resource name, e.g. api=oday-plus-api. The resource "
            "kind is taken from the release toolchain inventory, never supplied here."
        ),
    )
    parser.add_argument("--gcloud", default="gcloud")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write the absence readback receipt for the build phase.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="Re-verify an admitted manifest's initial-release recovery before deploy.",
    )
    parser.add_argument("--receipt", type=Path, default=None)
    args = parser.parse_args(argv)

    if bool(args.output) == bool(args.manifest):
        print(
            "請擇一指定 --output（build 階段產生 readback）或 --manifest"
            "（admission 階段重驗）。",
            file=sys.stderr,
        )
        return 2

    targets = dict(_parse_target(raw) for raw in args.target)

    try:
        if args.manifest is not None:
            manifest, manifest_errors = load_manifest(args.manifest)
            if manifest_errors or manifest is None:
                raise ProbeError(
                    [f"無法載入 release manifest：{error}" for error in manifest_errors]
                )
            if manifest.get("initial_release_recovery") is None:
                # 一般 release 綁的是 rollback_release，這個檢查對它沒有意見。
                print(
                    "manifest 未使用 initial-release recovery；沿用既有 rollback "
                    "綁定，無需 target 讀回。"
                )
                return 0
            receipt = verify_manifest_recovery(
                manifest,
                environment=args.environment,
                project=args.project,
                region=args.region,
                targets=targets,
                gcloud=args.gcloud,
            )
            if args.receipt is not None:
                _write_json(args.receipt, receipt)
            print(
                "initial-release recovery 重驗通過："
                f"environment={receipt['target_environment']} "
                f"candidate_sha={receipt['candidate_sha']} "
                f"manifest_digest={receipt['manifest_digest']} "
                f"recovery_method={receipt['recovery_method']}"
            )
            return 0

        readback = probe_target_absence(
            target_environment=args.environment,
            project=args.project,
            region=args.region,
            targets=targets,
            gcloud=args.gcloud,
        )
    except ProbeError as exc:
        print("無法採用 initial-release recovery：", file=sys.stderr)
        for error in exc.errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    _write_json(args.output, readback)
    print(
        "target 讀回完成，未發現任何既有 release："
        f"environment={readback['target_environment']} "
        f"project={readback['project']} region={readback['region']} "
        f"targets={len(readback['targets'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Runtime Release 的 GitHub environment 綁定檢查：環境級變數是否真的解析得到。

為什麼需要這一關
----------------

`odayplus` 的 repository 層級 Actions variables 是**空的**，`GCP_PROJECT_ID`、
`GCP_AR_REPO`、`GCP_WORKLOAD_IDENTITY_PROVIDER` 這些值全部只存在於 `dev` /
`staging` / `production` 三個 GitHub environment 之下。GitHub 只有在 job 帶了
`environment:` 綁定時才會把該 environment 的 variables 注入 `vars.*`；沒有綁定
時 `vars.X` 不會報錯，而是安靜地展開成空字串。

這一點在 build 階段特別危險：

* `HAS_WIF` 由 `vars.GCP_WORKLOAD_IDENTITY_PROVIDER != ''` 推導，沒綁定就恆為
  `false`，於是「缺少 OIDC」的拒絕理由會出現在**每一次** dispatch 上，包括
  設定其實完全正確的那些。
* 就算跳過那一關，`REPO_PATH` 會組成 `-docker.pkg.dev//`，image reference 變成
  沒有 registry、沒有專案的字串——build 會失敗在一個與真正原因無關的地方，或者
  更糟，推到非預期的位置。

而 build 階段**不能**直接綁定部署用的那個 environment：`staging` 與
`production` 都設了 `required_reviewers`，綁上去等於要求人類先核准一次「部署」
才能開始 build，而 build 正是產生 lease 所要驗證的 manifest 的環節。所以 build
階段綁定的是同名的 `-build` environment：同一組環境級變數，沒有部署核准規則。

這個模組是那個綁定的**執行期證據**。它只看變數在不在（永遠不看值、不印值），
缺少時以中文收據 fail closed，並且明確指出應該去哪個 GitHub environment 補。
綁定本身則由 `tests/ops/test_deploy_workflow_contract.py` 對 workflow YAML 靜態
把關——執行期無法自證綁定，能自證的是綁定失敗後的結果。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

VERIFIER_NAME = "delivery_toolchain/release/check_release_environment.py"
RECEIPT_KIND = "runtime-release-environment-binding"

# 每個 scope 需要哪些 GitHub environment 變數才有辦法完成工作。
# 這些名稱就是 GitHub variable 的名稱，step 的 `env:` 區塊必須逐一對應；
# contract test 會比對兩者，避免 workflow 少導一個變數卻沒人發現。
OIDC_VARIABLES = (
    "GCP_WORKLOAD_IDENTITY_PROVIDER",
    "GCP_SERVICE_ACCOUNT",
)

ARTIFACT_REGISTRY_VARIABLES = (
    "GCP_PROJECT_ID",
    "GCP_REGION",
    "GCP_AR_REPO",
)

STAGING_FOUNDATION_VARIABLES = (
    "GCP_PROJECT_ID",
    "GCP_REGION",
    "GCP_CLOUD_SQL_INSTANCE",
    "ODP_STAGING_VPC_NETWORK",
    "ODP_STAGING_VPC_SUBNETWORK",
    "ODP_STAGING_KMS_KEY_ID",
    "ODP_STAGING_DEPLOYER_SERVICE_ACCOUNT",
    "ODP_STAGING_TERRAFORM_STATE_BUCKET",
    "ODP_STAGING_RECOVERY_BUNDLE_BUCKET",
)

REQUIRED_VARIABLES: dict[str, tuple[str, ...]] = {
    # build 需要 OIDC 才能推 image，需要 registry 座標才能組出 image reference，
    # 需要四個 service/job 名稱才能決定要建哪四個 image。
    "build": (
        *OIDC_VARIABLES,
        *ARTIFACT_REGISTRY_VARIABLES,
        "ODP_CLOUD_RUN_API_SERVICE",
        "ODP_CLOUD_RUN_WEB_SERVICE",
        "ODP_CLOUD_RUN_WORKER_JOB",
        "ODP_CLOUD_RUN_SCHEDULER_JOB",
        # build 不部署，所以不需要任何 VPC 網路綁定（connector 或 Direct VPC 都
        # 不需要）。但 egress 模式是 build handoff 為 sources-off attestation 記錄
        # 的非 secret runtime fact（build_release_handoff.py 讀
        # ODP_CLOUD_RUN_VPC_EGRESS，缺值時以 resolved_cloud_run_egress=unresolved
        # fail closed），所以它留在 build scope，讓缺值在這一關就以中文收據被拒，
        # 而不是在 handoff 那一步以較難讀的錯誤爆掉。
        "ODP_CLOUD_RUN_VPC_EGRESS",
    ),
    # admission 不部署也不 build，它只需要能讀共用 lease 狀態並驗章。
    "admission": (
        *OIDC_VARIABLES,
        "ODP_RELEASE_LEASE_PUBLIC_KEY",
        "ODP_RELEASE_LEASE_STATE_URI",
    ),
    # deploy 以 digest 部署，migration job 是 build 不需要、deploy 需要的那一個。
    "deploy": (
        *OIDC_VARIABLES,
        *ARTIFACT_REGISTRY_VARIABLES,
        "ODP_CLOUD_RUN_API_SERVICE",
        "ODP_CLOUD_RUN_WEB_SERVICE",
        "ODP_CLOUD_RUN_MIGRATION_JOB",
        "ODP_CLOUD_RUN_WORKER_JOB",
        "ODP_CLOUD_RUN_SCHEDULER_JOB",
        # Sources-off is only safe when the actual deploy environment resolves
        # the egress mode; an empty vars.* expression otherwise silently falls
        # back to public Cloud Run egress. The network half of the binding is
        # one of two mutually exclusive modes (see VPC_BINDING_MODES) and is
        # checked separately by ``vpc_binding_errors``.
        "ODP_CLOUD_RUN_VPC_EGRESS",
    ),
    # Staging release-scoped names, endpoints, tenants, and service accounts
    # come from Terraform outputs. This gate admits only the long-lived
    # foundation needed to run Terraform and the lifecycle verifier.
    "staging": (
        *OIDC_VARIABLES,
        *STAGING_FOUNDATION_VARIABLES,
    ),
}

SCOPES = tuple(REQUIRED_VARIABLES)

# Cloud Run 接進 VPC 有兩種互斥的方式，deploy 階段必須剛好解析到其中一種：
#
# * ``connector``：Serverless VPC Access connector（dev / staging 沿用）。
# * ``direct_vpc``：Direct VPC egress，Cloud Run 直接掛在 VPC 的 subnetwork 上。
#   這是 ``infra/terraform/cloud_run.tf`` 對 production 宣告的架構
#   （``vpc_access { network_interfaces { network, subnetwork } }``），IaC 不會
#   產生任何 connector 資源，所以 production 的 GitHub environment 沒有、也不
#   該有 ``ODP_CLOUD_RUN_VPC_CONNECTOR``。變數名沿用 production environment 既有
#   的 ``ODP_PROD_VPC_NETWORK`` / ``ODP_PROD_VPC_SUBNETWORK``（與 staging
#   foundation 的 ``ODP_STAGING_VPC_*`` 同一命名慣例）；gate、deploy script 與
#   GitHub 變數三處使用同一組名字。
#
# 兩種模式都必須搭配 ``ODP_CLOUD_RUN_VPC_EGRESS``（列在 REQUIRED_VARIABLES）。
# 半套（只有 network 沒有 subnetwork）與兩套並存（connector 與 network 同時
# 有值）都 fail closed：前者 gcloud 會拒絕，後者 ``--vpc-connector`` 與
# ``--network`` 互斥，兩種情況都不該走到第一次 Cloud Run mutation 才發現。
VPC_BINDING_MODES: dict[str, tuple[str, ...]] = {
    "connector": ("ODP_CLOUD_RUN_VPC_CONNECTOR",),
    "direct_vpc": ("ODP_PROD_VPC_NETWORK", "ODP_PROD_VPC_SUBNETWORK"),
}

# 只有真正執行 Cloud Run mutation 的 scope 需要網路綁定。build 不部署；
# admission 只驗 lease；staging 的網路由 Terraform output 決定，不經這裡。
VPC_BINDING_SCOPES = ("deploy",)

VPC_BINDING_VARIABLES = tuple(
    name for names in VPC_BINDING_MODES.values() for name in names
)


def required_variables(scope: str) -> tuple[str, ...]:
    """回傳這個 scope 無條件必須解析得到的 GitHub environment 變數名稱。"""

    try:
        return REQUIRED_VARIABLES[scope]
    except KeyError:
        raise ValueError(
            f"未知的 scope {scope!r}；可用值為 {list(SCOPES)}。"
        ) from None


def declared_variables(scope: str) -> tuple[str, ...]:
    """回傳這個 scope 會讀取的全部變數：無條件必要的，加上二擇一的網路綁定。

    workflow 裡綁定檢查 step 的 ``env:`` 區塊必須逐一對應這個集合（contract
    test 會比對），否則某個模式的變數在 GitHub 上設好了、gate 卻讀不到。
    """

    names = list(required_variables(scope))
    if scope in VPC_BINDING_SCOPES:
        names.extend(VPC_BINDING_VARIABLES)
    return tuple(names)


def missing_variables(scope: str, values: dict[str, str | None]) -> list[str]:
    """回傳沒有解析到值的無條件必要變數名稱（保持宣告順序）。"""

    return [
        name
        for name in required_variables(scope)
        if not (values.get(name) or "").strip()
    ]


def _resolved(values: dict[str, str | None], name: str) -> bool:
    return bool((values.get(name) or "").strip())


def resolved_vpc_binding_mode(values: dict[str, str | None]) -> str | None:
    """回傳完整解析到的網路綁定模式名稱；沒有或不只一個時回傳 ``None``。"""

    complete = [
        mode
        for mode, names in VPC_BINDING_MODES.items()
        if all(_resolved(values, name) for name in names)
    ]
    if len(complete) != 1:
        return None
    return complete[0]


def vpc_binding_errors(
    scope: str, github_environment: str, values: dict[str, str | None]
) -> list[str]:
    """回傳 deploy 階段網路綁定的阻擋理由（中文）；空 list 代表剛好一種模式。"""

    if scope not in VPC_BINDING_SCOPES:
        return []

    errors: list[str] = []
    complete: list[str] = []
    for mode, names in VPC_BINDING_MODES.items():
        present = [name for name in names if _resolved(values, name)]
        missing = [name for name in names if not _resolved(values, name)]
        if present and missing:
            errors.append(
                f"{scope} 階段在 GitHub environment `{github_environment}` 的 "
                f"{mode} 網路綁定只設定了一半：已有 "
                + "、".join(present)
                + "，缺少 "
                + "、".join(missing)
                + "。半套綁定會在 Cloud Run mutation 時才被 gcloud 拒絕；請補齊或全部移除。"
            )
        elif present:
            complete.append(mode)

    if len(complete) > 1:
        errors.append(
            f"{scope} 階段在 GitHub environment `{github_environment}` 同時設定了 "
            + " 與 ".join(complete)
            + " 兩種網路綁定；`--vpc-connector` 與 `--network/--subnet` 互斥，"
            "請只保留實際架構使用的那一種。"
        )
    elif not complete and not errors:
        options = "；或 ".join(
            f"{mode}（" + "、".join(names) + "）"
            for mode, names in VPC_BINDING_MODES.items()
        )
        errors.append(
            f"{scope} 階段在 GitHub environment `{github_environment}` 取不到任何 "
            f"Cloud Run VPC 網路綁定，需要二擇一：{options}。"
            "沒有網路綁定的 sources-off 部署會走 Cloud Run 公網 egress，因此不得繼續執行。"
        )
    return errors


def binding_errors(
    *,
    scope: str,
    environment: str,
    github_environment: str,
    values: dict[str, str | None],
) -> list[str]:
    """回傳所有阻擋這個 job 繼續執行的理由（中文）；空 list 代表通過。"""

    errors: list[str] = []

    if scope not in REQUIRED_VARIABLES:
        errors.append(f"scope 必須是 {list(SCOPES)} 其中之一，實際值為 {scope!r}。")
        return errors

    if not environment.strip():
        errors.append("environment 不得為空；沒有目標環境就無從判斷該取哪一組變數。")

    if not github_environment.strip():
        errors.append(
            "github_environment 不得為空；這個 job 沒有 `environment:` 綁定時，"
            "`vars.*` 會安靜地展開成空字串而不是報錯。"
        )

    missing = missing_variables(scope, values)
    if missing:
        errors.append(
            f"{scope} 階段在 GitHub environment `{github_environment}` 取不到必要變數："
            + "、".join(missing)
            + "。這通常代表該 environment 不存在、或存在但沒有設定這些變數"
            "（GitHub 會為未建立的 environment 自動建一個空的），"
            "而不是變數的值有問題；請到該 environment 補齊後重跑。"
        )

    errors.extend(vpc_binding_errors(scope, github_environment, values))

    if scope == "staging":
        state_bucket = (values.get("ODP_STAGING_TERRAFORM_STATE_BUCKET") or "").strip()
        recovery_bucket = (values.get("ODP_STAGING_RECOVERY_BUNDLE_BUCKET") or "").strip()
        if state_bucket and recovery_bucket and state_bucket == recovery_bucket:
            errors.append(
                "staging 階段儲存邊界檢查失敗：ODP_STAGING_RECOVERY_BUNDLE_BUCKET "
                "不得與 ODP_STAGING_TERRAFORM_STATE_BUCKET 相同；"
                "recovery bundle 必須使用獨立受治理非 state 儲存，嚴禁寫入 Terraform state/lock-only bucket。"
            )
        if recovery_bucket.lower() in {"placeholder", "changeme", "dummy", "todo"}:
            errors.append(
                "staging 階段儲存邊界檢查失敗：ODP_STAGING_RECOVERY_BUNDLE_BUCKET 不得使用 placeholder 佔位值。"
            )

    return errors


def build_receipt(
    *,
    scope: str,
    environment: str,
    github_environment: str,
    release_sha: str,
    task_id: str,
    values: dict[str, str | None],
    errors: list[str],
    checked_at: datetime,
) -> dict[str, Any]:
    """組出中文 fail-closed 收據。收據只記錄變數「有沒有解析到」，永不記錄值。"""

    names = declared_variables(scope) if scope in REQUIRED_VARIABLES else ()
    required = required_variables(scope) if scope in REQUIRED_VARIABLES else ()
    resolved = {name: _resolved(values, name) for name in names}
    missing = [name for name in required if not resolved[name]]
    admitted = not errors

    # 網路綁定是二擇一，所以收據另外記「解析到哪一種模式」；只有 present/absent，
    # 不記 connector 名或網路名。
    vpc_binding: dict[str, Any] | None = None
    if scope in VPC_BINDING_SCOPES:
        vpc_binding = {
            "mode": resolved_vpc_binding_mode(values),
            "modes": {
                mode: {name: resolved[name] for name in mode_names}
                for mode, mode_names in VPC_BINDING_MODES.items()
            },
        }

    if admitted:
        summary = (
            f"{scope} 階段已綁定 GitHub environment `{github_environment}`，"
            f"{len(required)} 個必要環境變數全部解析成功"
        )
        if vpc_binding is not None:
            summary += f"，Cloud Run VPC 網路綁定模式為 {vpc_binding['mode']}"
        summary += "。"
    else:
        summary = (
            f"{scope} 階段被拒絕：GitHub environment `{github_environment}` "
            f"缺少 {len(missing)} 個必要變數"
        )
        if vpc_binding is not None and vpc_binding["mode"] is None:
            summary += "，且沒有剛好一種 Cloud Run VPC 網路綁定"
        summary += "；此階段不得繼續執行。"

    # The egress mode is a non-secret runtime fact. Recording its resolved
    # value lets the build handoff bind sources-off evidence to what GitHub
    # actually injected, while all credentials and identity values remain
    # presence-only.
    resolved_non_secret_values = {}
    if "ODP_CLOUD_RUN_VPC_EGRESS" in values:
        resolved_non_secret_values["ODP_CLOUD_RUN_VPC_EGRESS"] = (
            values.get("ODP_CLOUD_RUN_VPC_EGRESS") or ""
        ).strip()

    return {
        "receipt_kind": RECEIPT_KIND,
        "schema_version": 1,
        "verifier": VERIFIER_NAME,
        "scope": scope,
        "environment": environment,
        "github_environment": github_environment,
        "release_sha": release_sha,
        "task_id": task_id,
        "admitted": admitted,
        "checked_at": checked_at.astimezone(UTC).replace(microsecond=0).isoformat(),
        # 只有 present/absent。變數值（含 WIF provider 路徑與服務帳號）不進收據。
        "variables_resolved": resolved,
        "missing_variables": missing,
        "blockers_zh_tw": list(errors),
        "summary_zh_tw": summary,
        "secret_values_redacted": True,
        "resolved_non_secret_values": resolved_non_secret_values,
        "vpc_binding": vpc_binding,
    }


def _write_receipt(receipt: dict[str, Any], receipt_path: Path | None) -> None:
    if receipt_path is None:
        return
    try:
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(
            json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    except OSError as exc:
        print(f"warning: 無法寫入環境綁定收據 {receipt_path}: {exc}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", required=True, choices=SCOPES)
    parser.add_argument("--environment", required=True)
    # workflow 用來綁定的 environment 名稱。build 階段是 `<env>-build`，
    # admission/deploy 階段是 `<env>` 本身。
    parser.add_argument("--github-environment", required=True)
    parser.add_argument("--release-sha", default="")
    parser.add_argument("--task-id", default="")
    parser.add_argument("--receipt", type=Path, default=None)
    args = parser.parse_args(argv)

    # 變數值從 environment 讀，不從 argv 讀：argv 會出現在 process listing 與
    # 錯誤訊息裡，而這裡拿到的是可辨識雲端身分的字串。
    values = {name: os.environ.get(name) for name in declared_variables(args.scope)}

    errors = binding_errors(
        scope=args.scope,
        environment=args.environment,
        github_environment=args.github_environment,
        values=values,
    )
    receipt = build_receipt(
        scope=args.scope,
        environment=args.environment,
        github_environment=args.github_environment,
        release_sha=args.release_sha,
        task_id=args.task_id,
        values=values,
        errors=errors,
        checked_at=datetime.now(UTC),
    )
    _write_receipt(receipt, args.receipt)

    if errors:
        print("Runtime Release 環境綁定檢查未通過：", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print(receipt["summary_zh_tw"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

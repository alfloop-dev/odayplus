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
    ),
}

SCOPES = tuple(REQUIRED_VARIABLES)


def required_variables(scope: str) -> tuple[str, ...]:
    """回傳這個 scope 必須解析得到的 GitHub environment 變數名稱。"""

    try:
        return REQUIRED_VARIABLES[scope]
    except KeyError:
        raise ValueError(
            f"未知的 scope {scope!r}；可用值為 {list(SCOPES)}。"
        ) from None


def missing_variables(scope: str, values: dict[str, str | None]) -> list[str]:
    """回傳沒有解析到值的變數名稱（保持宣告順序）。"""

    return [
        name
        for name in required_variables(scope)
        if not (values.get(name) or "").strip()
    ]


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

    names = required_variables(scope) if scope in REQUIRED_VARIABLES else ()
    resolved = {name: bool((values.get(name) or "").strip()) for name in names}
    missing = [name for name, present in resolved.items() if not present]
    admitted = not errors

    if admitted:
        summary = (
            f"{scope} 階段已綁定 GitHub environment `{github_environment}`，"
            f"{len(names)} 個必要環境變數全部解析成功。"
        )
    else:
        summary = (
            f"{scope} 階段被拒絕：GitHub environment `{github_environment}` "
            f"缺少 {len(missing)} 個必要變數；此階段不得繼續執行。"
        )

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
    values = {name: os.environ.get(name) for name in required_variables(args.scope)}

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

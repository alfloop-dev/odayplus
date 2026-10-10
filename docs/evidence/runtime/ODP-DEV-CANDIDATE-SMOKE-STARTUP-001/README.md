# ODP-DEV-CANDIDATE-SMOKE-STARTUP-001 — 候選版本 smoke 啟動逾時的有界驗證

- **Task ID**: `ODP-DEV-CANDIDATE-SMOKE-STARTUP-001`
- **Owner**: `Claude`
- **Reviewer**: `Codex2`
- **Base**: `origin/dev` `0dd210dbe04fb420825abbddc08c8d3141de9ab1`
- **Date**: `2026-10-10`
- **性質**: 工程 source 修補；不是 live 部署驗收，也未證明根因

## 1. 觸發事實（來自 continuation handoff，未重新收集）

Runtime Release `38025605232` attempt 2（SHA `0dd210dbe04f…`）artifact `11665197417`：

- 08:45:31 migration compatibility 對既有流量 revision：`/platform/health` 200、0.221s、`attempt_count=1`。這是既有 revision，不是 candidate 已暖機的證明。
- 08:49:05 tagged candidate smoke：`/platform/version`、`/platform/health` 各自 `The read operation timed out`；隨後 `/readiness` 200（live PostgreSQL / operator repository provenance），candidate bootstrap 亦有 live 回答。
- smoke refusal 後 traffic 已回復；沒有 final dev-admin gate receipt。

與 0% 流量 tagged candidate 冷啟動相容，但**不足以證明根因**；service-level `minScale=1` 不代表 tagged candidate revision 已暖機。

## 2. 修補內容（`product_ops/deployment/validate_cloud_run_live_deployment.py`）

- candidate smoke 的四個 API GET（`/platform/version`、`/platform/health`、`/readiness`、`/api/v1/operator/bootstrap`）改走既有的 `probe_with_bounded_retry` / `ProbeRetryPolicy`（與 compatibility gate 同一套分類），沒有新框架。
- 只有 `no_response`（逾時、URLError、連線重設等完全沒收到回應）可重試。任何**收到的回應**——401/403、5xx、非 JSON、非 object JSON、錯誤 release SHA、不健康 readiness/DB/provider/model——第一次就定案、零重試；無法建立的 URL（`invalid_request`）亦零重試。
- `smoke` CLI 預設：`attempts=4`、per-attempt timeout 沿用 `--timeout`（15s）、backoff `2s` 指數、上限 `8s`、總預算 `180s`。總預算是**全部 API probe 共用一個**（`deadline_scope=shared_by_api_probes`），每次 attempt 的 timeout 夾到剩餘預算；預算用完就不送出請求並 fail closed。新增 `--smoke-retry-*` 旗標；NaN/inf/負值/0 次一律以 `smoke:retry_policy` fail closed。
- 直接呼叫 `smoke_checks()` 而未給 policy 時維持單次嘗試（原行為）。
- 每個原本的 smoke check 名稱與判定不變，且只依真實回答判定；沒有回答一律 fail（例如 version 用盡重試時 `release_sha` 檢查為 `actual=<missing>`）。
- report 新增 `probe_retry_policy` 與各 probe 的 `*_probe` 收據（每次 attempt 的 status、error、elapsed、provenance、transient），失敗的 attempt 全部保留不覆寫。headers（transport token、bearer）從不進入收據。
- `_json_request` 對無法解析的 body 改拋 `ResponseBodyError(ValueError)`，攜帶收到的 status 與 provenance；`probe_json_endpoint` 改經由它，compatibility gate 行為不變。

## 3. 未變更

deploy script、workflow、IAM、secret、config、帳號／角色、業務資料、模型、Web `/operator` redirect probe 均未修改；未讀取任何 credential、未做任何 live 部署或 runtime mutation。沒有全域延長或取消 deadline。

## 4. 回歸測試（`tests/ops/test_cloud_run_live_deployment.py`）

- 冷啟動後成功（read timeout / URLError / connection reset 三種）：全部原始 check 綠燈，失敗 attempt 保留於收據，backoff 依序 `2,4,2`。
- 用盡次數：fail closed，四次 attempt 收據齊全，`release_sha` 不被補判。
- 共用總預算：時鐘不超過 100s，readiness attempt 被夾到 11s，bootstrap 未送出請求即 fail。
- 收到回答零重試：401、403、503、malformed JSON、非 object JSON、錯誤 release SHA。
- 無法建立的 URL 零重試；未給 policy 時單次嘗試。
- CLI 預設 policy 寫入報告；不合法 policy fail closed。
- 每個情境都檢查 bearer、API/Web invoker token 不出現在 report 與 check detail。

## 5. 驗證

宣告的驗證（`git diff --check`、`uv run --frozen --python 3.12 pytest tests/ops/test_cloud_run_live_deployment.py -q`）由 `task_verification` 在最終 head 執行，receipt 存於 supervisor evidence store，結果另以 task note 公布。

## 6. 驗收仍保留

本修補合併後，仍需 exact-head 必要 CI 與 Codex2 獨立審查，之後以正常 signed immutable dev release 重跑；不得重建 partial `1b064`、不得移動 tag。live 部署成功與否、完整系統驗收皆未因本 task 成立。

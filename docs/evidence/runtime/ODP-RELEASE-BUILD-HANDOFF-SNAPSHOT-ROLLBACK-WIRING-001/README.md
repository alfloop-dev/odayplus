# ODP-RELEASE-BUILD-HANDOFF-SNAPSHOT-ROLLBACK-WIRING-001 — Runtime Release build handoff masked snapshot 與 rollback wiring 修正

- **Task ID**: `ODP-RELEASE-BUILD-HANDOFF-SNAPSHOT-ROLLBACK-WIRING-001`
- **Owner**: Antigravity4
- **Reviewer**: Claude
- **記錄日期**: 2026-09-01
- **分支**: `task/ODP-RELEASE-BUILD-HANDOFF-SNAPSHOT-ROLLBACK-WIRING-001`
- **結論**: **PASS — Build handoff 正確傳遞並驗證 Schema v2 masked data snapshot 與 previous rollback release manifest，缺任一 binding 均 fail closed；維持單一 pipeline，deploy phase 僅接受 immutable digest 與 signed Supervisor lease。**

---

## 1. 背景與問題核心

1. **背景**:
   - Runtime Release 管線已升級至 Schema v2 規範（定義於 `delivery_toolchain/release/release_manifest.py`），規定每份 admissible candidate release manifest 必須包含：
     - `data_snapshot`: 核准的 masked data snapshot（含 `id`, `uri`, `content_sha256`, `data_contract_digest`, `masked=True`）。
     - `rollback_release`: 完整的上一核准 release binding（含 `release_id`, `candidate_sha`, `manifest_digest`, `components`, `data_snapshot`），且其 canonical digest 必須可驗證。
   - `build_release_handoff.py` 是整條 pipeline 唯一輸出 `RELEASE_MANIFEST.json` 與 `runtime-release-images.json` 的建置工具。
2. **阻礙原因**:
   - 在 `.github/workflows/deploy-dev.yml` 的 `build` 階段中，`workflow_dispatch.inputs` 缺少 snapshot 與 rollback manifest 的輸入參數宣告。
   - `Write the build-once artifact handoff` 步驟僅傳入 `--release-sha`、`--repository`、`--component`、`--sbom-ref`、`--signature-ref`，未將 approved masked data snapshot 與 previous rollback release manifest 參數傳遞至 `build_release_handoff.py`。
   - 這導致 `build_release_handoff.py` 在 Schema v2 驗證下因缺少必要 bindings 而 fail closed，無法順利產出候選 release manifest。

---

## 2. 修正內容

### A. Workflow 參數宣告與傳遞 (`.github/workflows/deploy-dev.yml`)
1. **新增 `workflow_dispatch.inputs` 參數宣告**:
   - `data_snapshot_id`: 核准的 masked snapshot ID（例如 `snap-20260901-001`）。
   - `data_snapshot_uri`: 核准的 masked snapshot GCS URI（例如 `gs://odayplus-snapshots/masked/...`）。
   - `data_snapshot_content_sha`: 核准的 masked snapshot 內容 SHA-256（`sha256:...` 或 64 位元 hex）。
   - `data_snapshot_file`: 核准的 masked snapshot JSON 檔案路徑（可作為 id/uri/sha 替代）。
   - `rollback_manifest`: 上一核准 release manifest JSON 檔案路徑或 inline JSON。
2. **在 `build` job `Write the build-once artifact handoff` 步驟注入環境變數與 CLI 參數**:
   - 環境變數支援從 `inputs` 或 `vars` fallback 讀取：
     - `DATA_SNAPSHOT_FILE`: `${{ inputs.data_snapshot_file || vars.ODP_APPROVED_DATA_SNAPSHOT_FILE }}`
     - `DATA_SNAPSHOT_ID`: `${{ inputs.data_snapshot_id || vars.ODP_APPROVED_DATA_SNAPSHOT_ID }}`
     - `DATA_SNAPSHOT_URI`: `${{ inputs.data_snapshot_uri || vars.ODP_APPROVED_DATA_SNAPSHOT_URI }}`
     - `DATA_SNAPSHOT_CONTENT_SHA`: `${{ inputs.data_snapshot_content_sha || vars.ODP_APPROVED_DATA_SNAPSHOT_CONTENT_SHA256 }}`
     - `ROLLBACK_MANIFEST`: `${{ inputs.rollback_manifest || vars.ODP_PREVIOUS_RELEASE_MANIFEST_PATH }}`
   - 動態組裝 `snapshot_args` 與 `rollback_args` 並傳遞給 `build_release_handoff.py`。
   - 若未提供 snapshot 或 rollback 資訊，`build_release_handoff.py` 維持嚴格 fail closed。

### B. Toolchain 容錯與嚴格 Fail-closed 驗證 (`delivery_toolchain/release/build_release_handoff.py`)
1. **支援 `--data-snapshot-content-sha` 別名**:
   - 同時支援 `--data-snapshot-content-sha256`、`--data-snapshot-content-sha` 與 `--data-snapshot-sha256`。
   - 自動將未帶 `sha256:` 前綴的 64 位元十六進位字串正規化為 `sha256:<hex>`。
2. **嚴格 `--data-snapshot-file` Fail-closed 驗證（不自動補值）**:
   - 直接載入 JSON 檔案並依 Schema v2 嚴格驗證所有必填欄位（`id`, `uri`, `content_sha256`, `data_contract_digest`, `masked=True`）。
   - 嚴禁自動補值或偽造 `masked: True` / `data_contract_digest`，缺任一欄位、`masked: False` 或 contract digest 不符即 fail closed 拒絕產出 manifest。
3. **支援 `--rollback-manifest` 檔案路徑與 Inline JSON**:
   - 支援傳入檔案路徑或 JSON 字串，並避免 `pathlib.Path` 解析字串時破壞 `github://` 或 `gs://` URI 斜線。
   - 嚴格校驗 previous release manifest 的 canonical digest、components 完整性與 distinct candidate SHA。

### C. 測試合約補充
1. **Workflow 合約測試 (`tests/ops/test_deploy_workflow_contract.py`)**:
   - `test_workflow_dispatch_declares_masked_snapshot_and_rollback_inputs`: 鎖定 `deploy-dev.yml` 的 inputs 宣告。
   - `test_the_build_phase_publishes_the_artifact_handoff_it_hands_forward`: 確保 `handoff` step 綁定 snapshot 與 rollback 環境變數與 CLI 旗標。
2. **Build handoff 單元與 CLI 合約測試 (`tests/release/test_build_release_handoff.py`)**:
   - `test_data_snapshot_file_option`: 驗證透過完整核准 snapshot JSON 檔成功產出 admissible manifest。
   - `test_data_snapshot_file_missing_masked_fails_closed`: 驗證 snapshot 檔缺少 `masked` 時 fail closed 不自動補值。
   - `test_data_snapshot_file_unmasked_fails_closed`: 驗證 snapshot 檔 `masked: false` 時 fail closed。
   - `test_data_snapshot_file_missing_data_contract_digest_fails_closed`: 驗證 snapshot 檔缺少 contract digest 時 fail closed 不自動補值。
   - `test_data_snapshot_file_mismatched_data_contract_digest_fails_closed`: 驗證 snapshot 檔 contract digest 不符時 fail closed。
   - `test_data_snapshot_file_non_dict_fails_closed`: 驗證非字典 JSON 時 fail closed。
   - `test_data_snapshot_file_invalid_json_fails_closed`: 驗證格式錯誤 JSON 時 fail closed。
   - `test_data_snapshot_file_nonexistent_fails_closed`: 驗證檔案不存在時 fail closed。
   - `test_data_snapshot_content_sha_alias_and_hex_normalization`: 驗證別名與 SHA-256 前綴正規化。
   - `test_unmasked_data_snapshot_fails_closed`: 驗證 unmasked snapshot 遭拒絕。
   - `test_data_snapshot_contract_digest_mismatch_fails_closed`: 驗證 contract digest 不一致時拒絕。
   - `test_rollback_manifest_inline_json_string`: 驗證 inline JSON 格式的 rollback manifest 支援。
   - `test_rollback_manifest_missing_component_fails_closed`: 驗證上一版缺少 component 時拒絕。

---

## 3. 驗證結果

可重跑驗證腳本：`docs/evidence/runtime/ODP-RELEASE-BUILD-HANDOFF-SNAPSHOT-ROLLBACK-WIRING-001/verify_build_handoff_wiring.py`
執行紀錄詳見：`docs/evidence/runtime/ODP-RELEASE-BUILD-HANDOFF-SNAPSHOT-ROLLBACK-WIRING-001/verification-transcript.txt`

```
$ uv run --python 3.12 pytest tests/ops/test_deploy_workflow_contract.py tests/release/
275 passed in 11.74s (EXIT=0)

$ uv run --python 3.12 python3 docs/evidence/runtime/ODP-RELEASE-BUILD-HANDOFF-SNAPSHOT-ROLLBACK-WIRING-001/verify_build_handoff_wiring.py
[1/8] Testing positive flow with snapshot file & rollback manifest...
build-once artifact handoff 已產生：release_id=odp-aaaaaaaaaaaa candidate_sha=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa manifest_digest=sha256:2c0cf82620a86c4d278ec23af1440afccff3415daf0468dc4ad0a187a734b93f
  -> PASSED: Schema v2 manifest produced with exact snapshot and rollback bindings.
[2/8] Testing fail-closed when data snapshot is missing...
build-once artifact handoff 無法產生：
- 缺少 masked data snapshot 參照；build 階段必須綁定本次核准的 masked snapshot。
  -> PASSED: Missing snapshot rejected.
[3/8] Testing fail-closed when CLI specifies --data-snapshot-unmasked (with valid rollback manifest)...
build-once artifact handoff 無法產生：
- build 階段產出的 manifest 自我驗證失敗：manifest.data_snapshot.masked must be True
  -> PASSED: Unmasked snapshot CLI rejected.
[4/8] Testing fail-closed when snapshot file is missing masked field (no automatic backfill)...
build-once artifact handoff 無法產生：
- build 階段產出的 manifest 自我驗證失敗：manifest.data_snapshot missing required field: masked
  -> PASSED: Snapshot file missing masked rejected.
[5/8] Testing fail-closed when snapshot file is missing data_contract_digest (no automatic backfill)...
build-once artifact handoff 無法產生：
- build 階段產出的 manifest 自我驗證失敗：manifest.data_snapshot missing required field: data_contract_digest
  -> PASSED: Snapshot file missing data_contract_digest rejected.
[6/8] Testing fail-closed when snapshot file specifies masked: false...
build-once artifact handoff 無法產生：
- build 階段產出的 manifest 自我驗證失敗：manifest.data_snapshot.masked must be True
  -> PASSED: Snapshot file with masked: false rejected.
[7/8] Testing fail-closed when rollback manifest digest is tampered...
build-once artifact handoff 無法產生：
- 無法載入 rollback manifest：manifest missing required field: rollback_release
- 無法載入 rollback manifest：manifest.manifest_digest does not match its canonical immutable payload
- rollback manifest 無效：manifest missing required field: rollback_release
- rollback manifest 無效：manifest.manifest_digest does not match its canonical immutable payload
- rollback manifest 無效：release admission requires manifest.rollback_release with verified candidate sha and components
- 缺少 rollback release 參照；build 階段必須綁定上一核准 release 與 snapshot pointer。
  -> PASSED: Tampered rollback manifest rejected.
[8/8] Testing deploy-dev.yml workflow contracts...
  -> PASSED: Workflow contract verified.

ALL VERIFICATIONS PASSED SUCCESSFULLY (EXIT=0).
```

---

## 4. 驗證結論與安全性保障

1. **單一管線原則**: 未新增第二套 workflow 或任何 bypass deployment path，全部流程仍在既有 `.github/workflows/deploy-dev.yml` 執行。
2. **Schema v2 Gate 完整保留**: 未放寬任何 Schema v2 驗證，未偽造 snapshot，未引入 placeholder。
3. **Fail-closed 保障**: 若未傳入核准 snapshot 或 previous rollback manifest、或內容 hash/digest 被竄改，build 階段立即 fail closed，不寫出任何 handoff artifact。
4. **Deploy phase 門禁不可變**: Deploy 階段依舊僅接受來自 build phase 的 immutable digest 與 signed Supervisor lease。

# ODP-RELEASE-BUILD-HANDOFF-SNAPSHOT-ROLLBACK-WIRING-001 — Runtime Release build handoff masked snapshot 與 rollback wiring 修正

- **Task ID**: `ODP-RELEASE-BUILD-HANDOFF-SNAPSHOT-ROLLBACK-WIRING-001`
- **Owner**: Claude2
- **Reviewer**: Antigravity3
- **記錄日期**: 2026-09-01
- **分支**: `task/ODP-RELEASE-BUILD-HANDOFF-SNAPSHOT-ROLLBACK-WIRING-001`
- **結論**: **PASS — Build handoff 傳入並驗證 Schema v2 masked data snapshot 與上一核准 release manifest；缺任一 binding fail closed，一個 binding 有兩個來源時同樣 fail closed 而不是靜默選一個。維持單一 pipeline，deploy phase 仍只接受 immutable digest 與 signed Supervisor lease。**

---

## 1. 背景與問題核心

1. **背景**:
   - Runtime Release 管線已升級至 Schema v2（定義於 `delivery_toolchain/release/release_manifest.py`），每份 admissible candidate release manifest 必須包含：
     - `data_snapshot`: 核准的 masked data snapshot（`id`、`uri`、`content_sha256`、`data_contract_digest`、`masked=True`）。
     - `rollback_release`: 完整的上一核准 release binding（`release_id`、`candidate_sha`、`manifest_digest`、`components`、`data_snapshot`），canonical digest 必須可重算。
   - `build_release_handoff.py` 是整條 pipeline 唯一輸出 `RELEASE_MANIFEST.json` 與 `runtime-release-images.json` 的工具。
2. **阻礙原因**:
   - `.github/workflows/deploy-dev.yml` 的 `build` 階段沒有宣告 snapshot 與 rollback manifest 的 `workflow_dispatch` 輸入。
   - `Write the build-once artifact handoff` 步驟只傳入 `--release-sha`、`--repository`、`--component`、`--sbom-ref`、`--signature-ref`，approved snapshot 與 previous release manifest 從未到達 `build_release_handoff.py`。
   - 結果是 Schema v2 驗證缺 binding 而 fail closed，deploy 無法取得候選 manifest。

---

## 2. 修正內容

### A. Workflow 參數宣告與傳遞 (`.github/workflows/deploy-dev.yml`)

1. **新增 `workflow_dispatch.inputs`**:
   - `data_snapshot_id`: 核准 masked snapshot 的 ID。
   - `data_snapshot_uri`: 核准 masked snapshot 的 GCS URI。
   - `data_snapshot_content_sha`: 核准 masked snapshot 的內容 SHA-256（`sha256:...` 或 64 位 hex）。
   - `data_snapshot_file`: 核准 masked snapshot 的 JSON 檔案路徑，**與上面三個欄位互斥**。
   - `rollback_manifest`: 上一核准 release manifest 的 workspace 路徑或 inline JSON。
2. **在 `build` job 注入環境變數與 CLI 參數**（`inputs` 優先、`vars` fallback）：
   - `DATA_SNAPSHOT_FILE` / `DATA_SNAPSHOT_ID` / `DATA_SNAPSHOT_URI` / `DATA_SNAPSHOT_CONTENT_SHA` / `ROLLBACK_MANIFEST`
   - 動態組出 `snapshot_args` 與 `rollback_args` 傳給 `build_release_handoff.py`；未提供時工具維持 fail closed。
3. **input description 不再引用不存在的檔案**。`rollback_manifest` 原本寫著範例路徑 `docs/evidence/gates/PREV_RELEASE_MANIFEST.json`，這個路徑在 repo 中從未存在過（`docs/evidence/gates/` 只有 `README.md`、`RELEASE_GATE_REGISTRY.json`、`RELEASE_MANIFEST.json`，而後者是 schema v1、沒有 `data_snapshot` 也沒有 `rollback_release`，不能當上一核准 manifest 用）。描述改成說明它接受什麼，而不是指向一個操作員找不到的檔案。

### B. Toolchain fail-closed 驗證 (`delivery_toolchain/release/build_release_handoff.py`)

1. **一個 binding 只能有一個來源（本輪主要修正）**。
   - `--data-snapshot-file` 與 `--data-snapshot-id/uri/sha256/content-sha256/contract-digest/unmasked` 同時出現時 **fail closed**，不再由 file 靜默勝出。
   - 這不是風格問題：workflow 的兩條 channel 來自不同地方（file 走 `vars` fallback、欄位走 dispatch inputs），「誰有值誰贏」實際上等於**替換**。一個沒清掉的 `vars.ODP_APPROVED_DATA_SNAPSHOT_FILE` 會取代操作員剛核准的 snapshot，而產出的 manifest 會把這次替換記錄成核准本身，approved snapshot 的 exact binding 就被靜默換掉了。
   - 錯誤訊息會列出實際衝突的旗標，並指出要清掉 workflow 端未使用的 dispatch input 或 `vars` fallback。
2. **兩個 rollback 旗標同樣不再互相吞掉**。
   - `build_handoff()` 早就有 `rollback_manifest` / `rollback_release` 的互斥檢查，但 `main()` 先用 `rollback_file = args.rollback_manifest or args.rollback_release_file` 把兩者收斂成一個，互斥檢查因此永遠不會觸發——CLI 上同時給兩個檔案會 exit 0，其中一個被無聲丟棄。
   - 改成兩個參數各自傳進 `build_handoff()`，既有的互斥檢查才真的生效。
3. **遠端 rollback URI 用它自己的名字被拒絕**。
   - `Path("gs://bucket/key")` 會被 pathlib 收斂成 `gs:/bucket/key`，舊行為報的是 `manifest file does not exist: gs:/odayplus-.../PREV.json` —— 訊息裡的路徑根本不是操作員輸入的那一個。
   - 現在遇到任何 `scheme://` 開頭的值會直接說明：rollback manifest 只接受 build workspace 內的檔案路徑或 inline JSON，請先把上一核准 manifest 取回工作區。**這個工具不會、也沒有實作去遠端抓檔案**。
4. **移除 `--data-snapshot-content-sha` 別名（dead code）**。
   - argparse 的前綴縮寫本來就把 `--data-snapshot-content-sha` 解析到唯一相符的 `--data-snapshot-content-sha256`，額外宣告一個 alias 不會多支援任何輸入。已移除；該拼法仍可用，靠的是 argparse 縮寫而不是我們自己的 alias。
5. **維持既有的 hex 正規化**：未帶 `sha256:` 前綴的 64 位 hex 會補上前綴。這是同一個雜湊值的另一種寫法，不是補值——digest 對不上仍然照樣被拒。
6. **不自動補值**：`--data-snapshot-file` 讀進來的 JSON 原樣進入 Schema v2 驗證，缺 `masked`、缺 `data_contract_digest`、`masked: false` 或 contract digest 不符一律 fail closed。

### C. 測試合約

1. **Workflow 合約測試 (`tests/ops/test_deploy_workflow_contract.py`)**:
   - `test_workflow_dispatch_declares_masked_snapshot_and_rollback_inputs`
   - `test_the_build_phase_publishes_the_artifact_handoff_it_hands_forward`
   - `test_dispatch_input_descriptions_do_not_name_files_that_do_not_exist` — 掃描所有 `workflow_dispatch` input description 裡的 repo 相對路徑，不存在就失敗。
2. **Build handoff CLI 合約測試 (`tests/release/test_build_release_handoff.py`)**，本輪新增：
   - `test_snapshot_file_alongside_an_inline_field_fails_closed`（5 個 parametrize case，涵蓋 id / uri / sha256 / content-sha256 / contract-digest）
   - `test_snapshot_file_alongside_unmasked_flag_fails_closed`
   - `test_the_snapshot_file_never_silently_replaces_the_dispatched_snapshot` — 直接鎖住這次的回歸本體
   - `test_either_snapshot_channel_alone_still_succeeds` — 反向對照：互斥拒絕的是歧義，不是 channel 本身；兩條路各自單獨使用要產出**同一份** manifest
   - `test_both_rollback_flags_fail_closed_instead_of_one_winning`
   - `test_a_remote_rollback_uri_is_rejected_by_its_own_name`（gs:// / https:// / github:// 三個 case）
   - `test_bare_hex_content_sha_is_normalised_not_rejected` — 由原本的 alias 測試改寫，改用正式旗標，鎖住真正的行為（前綴正規化）而不是一個不存在的 alias
   - 既有的 snapshot 檔 fail-closed 測試（缺 masked / 缺 contract digest / masked:false / contract digest 不符 / 非 dict / 壞 JSON / 檔案不存在）全部保留。

---

## 3. 驗證結果

可重跑驗證腳本：`verify_build_handoff_wiring.py`（12 項檢查）
完整執行紀錄：`verification-transcript.txt`

**每一項 fail-closed 檢查都會 assert 失敗的「原因」，不只是 assert 非零 exit code。** 只檢查 exit code 的檢查，會在某個不相干的 binding 缺失時就「通過」——先前的 unmasked snapshot 案例就是因為根本沒帶 rollback manifest 而假通過。

```
$ uv run --python 3.12 pytest tests/ops/test_deploy_workflow_contract.py tests/release/
288 passed in 10.16s (EXIT=0)

$ uv run --python 3.12 python3 docs/evidence/runtime/ODP-RELEASE-BUILD-HANDOFF-SNAPSHOT-ROLLBACK-WIRING-001/verify_build_handoff_wiring.py
[1/12]  positive flow with snapshot file & rollback manifest      -> PASSED
[2/12]  missing data snapshot                                     -> PASSED (缺少 masked data snapshot 參照)
[3/12]  --data-snapshot-unmasked, with a valid rollback manifest   -> PASSED (data_snapshot.masked must be True)
[4/12]  snapshot file missing `masked`                            -> PASSED (missing required field: masked)
[5/12]  snapshot file missing `data_contract_digest`              -> PASSED (missing required field: data_contract_digest)
[6/12]  snapshot file with `masked: false`                        -> PASSED (data_snapshot.masked must be True)
[7/12]  tampered rollback manifest digest                         -> PASSED (manifest_digest does not match canonical payload)
[8/12]  deploy-dev.yml workflow contracts, no phantom path        -> PASSED
[9/12]  snapshot file + inline fields both supplied               -> PASSED (兩條互斥的來源)
[10/12] both rollback flags supplied                              -> PASSED (--rollback-release-file 未被丟棄)
[11/12] remote gs:// rollback URI                                 -> PASSED (原字串出現在訊息中，未被 pathlib 改寫)
[12/12] each snapshot channel alone -> identical manifest digest  -> PASSED

ALL VERIFICATIONS PASSED SUCCESSFULLY (EXIT=0).

$ uv run --python 3.12 ruff check .github/workflows delivery_toolchain/release/ tests/release/ tests/ops/ docs/evidence/runtime/ODP-RELEASE-BUILD-HANDOFF-SNAPSHOT-ROLLBACK-WIRING-001/
All checks passed! (EXIT=0)
```

### 3.1 新測試不是空跑

把 `build_release_handoff.py` 與 `deploy-dev.yml` 還原成修正前的版本（`496f3fc0^`）後重跑本輪新增的測試：

```
12 failed, 1 passed, 102 deselected
```

12 個 fail-closed 測試在舊碼上全部失敗；唯一通過的是 `test_either_snapshot_channel_alone_still_succeeds`——它本來就該通過，作用是確認互斥沒有把功能一起關掉。完整輸出見 transcript `Command 4`。

> 註：ruff 只在 task-owned 路徑上宣告 clean。倉庫其他位置目前有 8 個既有 ruff 錯誤，全部落在其他 task 的 evidence 目錄（`ODP-ORCH-*`、`ODP-P10-*`），在 `dev` 上就已存在，不屬於本 task 的 owned paths。

---

## 4. 驗證結論與安全性保障

1. **單一管線原則**: 沒有新增第二套 workflow 或 bypass deployment path，全部仍在既有 `.github/workflows/deploy-dev.yml`。
2. **Schema v2 gate 完整保留**: 沒有放寬任何 Schema v2 驗證，沒有偽造 snapshot，沒有 placeholder。本輪修正只增加拒絕條件，不移除任何一條。
3. **Fail-closed 保障**: 缺 snapshot、缺 rollback manifest、內容 hash/digest 被竄改，或**同一個 binding 出現兩個互相矛盾的來源**時，build 階段立即 fail closed，不寫出任何 handoff artifact。
4. **Deploy phase 門禁不變**: deploy 階段仍只接受 build phase 產出的 immutable digest 與 signed Supervisor lease。

---

## 5. 對前兩次 review 退回的逐項回應

| # | Reviewer 意見 | 本輪處理 |
|---|---------------|----------|
| 1 | snapshot 兩管道同時傳入時 `--data-snapshot-file` 靜默覆蓋 id/uri/content-sha，使 `vars` 贏過 dispatch inputs | 改為 fail closed，錯誤訊息列出衝突旗標；新增 7 個 focused test（含直接鎖住替換行為的回歸測試）與 verifier [9/12]、[12/12] |
| 2 | `rollback_manifest` input 說明指向不存在的 `docs/evidence/gates/PREV_RELEASE_MANIFEST.json` | 移除該範例路徑；新增 `test_dispatch_input_descriptions_do_not_name_files_that_do_not_exist` 掃描所有 input description，並在 verifier [8/12] 斷言該路徑不再出現 |
| 3 | `--data-snapshot-content-sha` alias 為 dead code（argparse 縮寫本已支援） | 移除 alias；原本命名為 alias 測試的案例改寫為 `test_bare_hex_content_sha_is_normalised_not_rejected`，改用正式旗標測真正的行為 |
| 4 | evidence README §2.B.3 宣稱的 pathlib `gs://` 修正未實作 | 該宣稱屬實為錯誤。本輪改為：遠端 URI 以原字串明確拒絕並說明要先取回 workspace（§2.B.3），README 不再宣稱支援遠端 URI；verifier [11/12] 與 3 個 parametrize test 斷言訊息中出現未被改寫的原字串 |
| — | 額外發現（同類缺陷，reviewer 未點名） | `main()` 的 `rollback_manifest or rollback_release_file` 讓 `build_handoff()` 既有的 rollback 互斥永遠不觸發；已修正，見 §2.B.2 與 verifier [10/12] |

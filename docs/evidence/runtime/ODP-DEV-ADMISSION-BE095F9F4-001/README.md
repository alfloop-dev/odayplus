# ODP-DEV-ADMISSION-BE095F9F4-001：dev admission 換綁到 be095f9f4

本資料夾記錄候選版本 `be095f9f405fb98b9a3f4f552c895702f252fab9` 進入 dev 的 registry 綁定。目的是做一次簽章手動 dev 部署，讓自動 dev 部署恢復運作。這裡只有 evidence，還沒有 lease，也還沒有部署。

## 為什麼需要手動部署

目前每一次 `phase=auto` 的 dev 部署，都會在 `Validate release phase inputs` 這一步被拒。be095f9f4 自己的自動部署 [44006695094](https://github.com/alfloop-dev/odayplus/actions/runs/44006695094) 也一樣，拒絕理由是：

> Latest dev deploy (run 43746746828 attempt 1) started its live step and did not succeed, and no live read-back proves the previous release was restored …

經過如下：

1. Run [43746746828](https://github.com/alfloop-dev/odayplus/actions/runs/43746746828) 在 attempt 1 部署 326afc6，live E2E 因 dev 專屬的模型 registry 資料失敗。之後的讀回確認線上已還原到 `odp-60cf786952c7`，並留下讀回收據（artifact `dev-failed-live-step-readback-1`）。
2. 2026-11-16 有人對同一個 run 執行 rerun，前一次 attempt 的 artifact 就不再列出。`restoration_readback`（`delivery_toolchain/release/automatic_dev.py`）找不到收據，把 attempt 1 判為 `uncertain`，之後的自動部署全部被拒。
3. 依 `docs/deployment/ENVIRONMENTS.md`，解法是做一次簽章手動 dev 部署，並明確指定 rollback manifest。

另一個 Claude session 先為 4644da18e 準備了同樣的換綁（核准編號 `HUMANOPS-DEV-ADMISSION-20261117A`），但還沒 commit，PR #1719 就把 dev 推到 be095f9f4。那份換綁從未提交，也沒有使用。負責人對 be095f9f4 的精確組合重新核准。

## 綁定內容

| 項目 | 值 |
|---|---|
| 候選版本 | `be095f9f4`（dev tip，PR #1719 合併） |
| dev build | Runtime Release run [44008824347](https://github.com/alfloop-dev/odayplus/actions/runs/44008824347)，`release_profile=dev-admin` |
| rollback | 線上 release `odp-60cf786952c7`，取自 run [43648043409](https://github.com/alfloop-dev/odayplus/actions/runs/43648043409) 的 `deployed-release-manifest-dev`，以 inline JSON 傳入 build |
| manifest | `docs/evidence/gates/RELEASE_MANIFEST.json`，從 build artifact 原檔複製，digest `sha256:2474a689…a1496` |
| 候選版本自己的 CI | push CI [44001410032](https://github.com/alfloop-dev/odayplus/actions/runs/44001410032)：11/11 個 job 成功。merge group CI [43994081518](https://github.com/alfloop-dev/odayplus/actions/runs/43994081518)：10/11 成功，唯一未成功的是只負責 dispatch 的 automatic dev release job，它在 merge_group 本來就會 skip。兩次都實際跑了 7 個 product lane，全部成功。詳見 `candidate-ci-be095f9f4.json` |
| production npm audit | build 結果 0 個 finding，見 `build-npm-audit-receipt-44008824347.json` |

## Gates（admission_target=dev）

**gate-0 Code：passed。** 依據是上表的 exact-candidate CI。

**gate-1 Contract：passed。** 審查範圍是 `60cf78695..be095f9f4`，共 114 個 commit；60cf78695 是目前線上的版本。

| 項目 | 結果 |
|---|---|
| OpenAPI | 沒有破壞性變更：沒有移除任何操作或回應欄位，沒有新增必填參數，沒有縮小 enum 或改變型別。只新增 6 個 GET receipt 端點和 2 個 schema |
| 事件 | 沒有變更 |
| migration 007 | 新增一個可為 NULL 的欄位，可以 rollback |
| model-ready view | 沒有變更 |
| 模型 I/O | #1714 讓 forecastops 改綁 canonical schema。若 rollback 會 fail closed，不會產生錯誤輸出 |
| #1719 | 只把 ephemeral staging 的 rollback probe tag 改成 3 個字元，加一個測試；不影響任何契約 |

審查紀錄是 `contract-review-be095f9f4.json`。比對程式是 `openapi_breaking_diff.py.txt`，用 python3 執行即可重現結果。

**gate-4 Security and Privacy：passed-with-deviation。** product-security 在候選版本上通過，build 的 production npm audit 為 0。負責人接受以下兩項揭露偏差，只適用這個 dev 候選版本，review_by 為 2026-12-01：

1. **H01：** 四個附條件允許的 LGPL 案例，外部權威收據仍然缺少，不宣稱通過。
2. **dev 相依的 npm：** `npm audit --package-lock-only` 共 6 個 moderate，0 個 high 或 critical，沒有重新分類。

**其他 gate：** gate-2 退回 blocked。它先前對 4b2310c53 的 staging 處置已被取代，下一個 staging 候選版本需要重新處置。gate-3、gate-5、gate-6 不變，仍是 production 的 blocked 狀態。

## 人工決定

紀錄檔是 `humanops-dev-admission-authorization-20261117.json`。

- **決定內容：** 負責人蔡尚志在 Claude Code 對話中先選擇「我接手，先手動 dev 部署 be095f9f4」（2026-11-17T03:13:02Z）。看過列出全部精確組合與兩項偏差的請求後，再回覆「同意，整組執行」（2026-11-17T03:31:40Z）。
- **紀錄性質：** 由 Claude 轉錄，不是外部身分系統的簽章。
- **核准編號：** registry 使用 `HUMANOPS-DEV-ADMISSION-20261117B`，lease 使用 `HUMANOPS-DEV-LEASE-20261117B`。
- **授權範圍：** 一次 release request、一張短效 lease、一次 deploy phase；可以執行 dev DB migration；外部來源維持關閉；不涉及 staging 和 production。
- **後續用途：** 這次部署的 live E2E 也作為 be095f9f4 進 staging（鏈 Q）的 dev 驗證。77 項中只允許 `models:registry_refused` 與 `models:no_surrogate_markers` 失敗。

## 驗證

```bash
python3 delivery_toolchain/e2e/check_release_gate_registry.py --expected-sha be095f9f405fb98b9a3f4f552c895702f252fab9   # RELEASE STATE: GO
python3 -c "import json,sys; sys.path.insert(0,'.'); from delivery_toolchain.release.check_runtime_admission import registry_admission_errors as e; r=json.load(open('docs/evidence/gates/RELEASE_GATE_REGISTRY.json')); print(e(r, release_sha='be095f9f405fb98b9a3f4f552c895702f252fab9', environment='dev', expected_manifest_digest='sha256:2474a6892a97217a91efac02102a6944a5b0629ee7f3b949d914118ba29a1496'))"   # []
```

分支從 exact candidate 開出，沒有 merge 其他 dev 變更；`be095f9f4..head` 只改 `docs/evidence/`。

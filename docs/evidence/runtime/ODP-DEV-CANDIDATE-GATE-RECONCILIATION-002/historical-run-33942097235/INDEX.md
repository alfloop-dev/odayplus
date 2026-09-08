# 歷史候選 C04 封存索引 — candidate `04e1572f` / Runtime Release run `33942097235`

- 封存日期: 2026-09-08
- 封存者: Claude2（owner）
- Reviewer: Codex2

**這整個目錄是歷史紀錄，不是本次候選。** 目前的正式候選 C 是
`596b9c9a1788d952811a2bf8d4bba8a4e4d76b12`（Runtime Release run `34179791241`），
其證據在上一層目錄。本目錄中任何檔案都不得被解讀為對 C 的驗證、對 C 的綁定，
或任何形式的 GO；C04 的所有 gate 早已隨候選改綁而失效。

## 這個目錄封存了什麼

### A. run `33942097235` 的六份原始 artifact（raw bytes，未經編輯）

| 檔案 | Artifact 名稱 | Artifact ID | 封存檔 Raw SHA-256 |
|---|---|---|---|
| `RELEASE_MANIFEST.json` | `runtime-release-manifest-04e1572f802a54c2646ba678fe2975226dfbd7c4` | 9962288831 | `efe7bed05df8f176b053f448acc0c303d8b81786212a98fc5e56f27031e1f124` |
| `runtime-release-images.json` | `runtime-release-images-04e1572f802a54c2646ba678fe2975226dfbd7c4` | 9962288660 | `e177983c92b64b8bd1e9da524010d47712192237adf58c19fa56cbf5550ad23e` |
| `initial-release-absence-readback.json` | `initial-release-absence-readback-04e1572f802a54c2646ba678fe2975226dfbd7c4` | 9962288978 | `5e6aba3b690ecbbac394ea2706036bc3319a650a0dfdbad25a61785dca01897f` |
| `release-environment-receipt.json` | `release-environment-receipt-dev-build` | 9962159216 | `3ab6933caaa85ffa1fe0190226e951c09d2fe83dcd9b3192a1ad2910920f984a` |
| `npm-audit-receipt.json` | `release-npm-audit-receipt-dev` | 9962164394 | `2c4bdc4eb31b0a726adcc5383942437eb19dfaa4010f85ace33ae64d94be0c95` |
| `release-phase-receipt.json` | `release-phase-receipt-dev-build` | 9962156474 | `e0f6a2bf2fe9e71d531936f5d491deafa528fa3f09d1c78645d69d9902ec9f54` |

run `33942097235` 的 artifact 目前尚未過期（`expired: false`）。2026-09-08 重新下載並逐檔
`cmp`，六檔全部 `EXIT=0`；命令與輸出見上一層 `verification-transcript.txt` 第 6 節。

### B. C04 時期的說明、逐字紀錄與當時的驗證器（byte-exact，取自本分支歷史）

| 封存檔 | 來源 blob（commit `972cad87`） | git blob id | 封存檔 Raw SHA-256 |
|---|---|---|---|
| `README-C04-run33942097235.md` | `docs/evidence/runtime/ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002/README.md` | `6b19005d61979e8a8bcc3a0c16d883174be7270b` | `7ca0cfd2cbda40d55945b49005cd03402eac5c821eedc03f18827fcf14fd8f5f` |
| `verification-transcript-C04-run33942097235.txt` | `docs/evidence/runtime/ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002/verification-transcript.txt` | `2e88c8fa844627c0e2e71bf65514b6289e6ad433` | `8ebf27f236ca343db62ca4aab3d37213af9b251c59a8ceec0d10db64d7df997f` |
| `verify_live_artifact_binding-C04-run33942097235.sh` | `docs/evidence/runtime/ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002/verify_live_artifact_binding.sh` | `75369cbb8f1a03e574fe51dec74c685e393e4484` | `bdfd258916c1c22328c7841e0f2a50200644abdd19ce31f37d28e3155891ae92` |

封存方式為 `git show 972cad87:<path> > <封存檔>`；三份檔案的 `git hash-object` 與來源 blob id
完全相同（見上一層 `verification-transcript.txt` 第 5 節），因此 C04 當時的
**API archive digest 表、artifact ID 索引、來源與 raw-byte 比對索引、逐字命令輸出**都已完整保留，
不是重寫的摘要。

`README-C04-run33942097235.md` 內含的 API archive digest 欄位（`sha256:40f743e3…`、
`sha256:192d7c22…`、`sha256:e732be9f…`）即為 reviewer 先前要求另存的雜湊索引來源。

### C. C04 時期的驗證器為何一併封存

`verify_live_artifact_binding-C04-run33942097235.sh` 是綁死 C04 的固定驗證器。
封存它是為了讓 C04 的逐字紀錄可被重新對照，**它不驗證、也不得被用來宣稱驗證了新候選 C**；
新候選 C 的驗證由上一層現行的 `verify_live_artifact_binding.sh` 執行，該腳本要求明確傳入
下載目錄與候選 worktree。

## 這個封存不代表什麼

- 不代表 C04 仍是有效候選：候選已改綁至 C，C04 的七道 gate 全部失效。
- 不代表已部署：run `33942097235` 與 run `34179791241` 都只跑 build phase，deploy／lease 相關 job 皆 `skipped`。
- 不代表 Human/Ops GO：`release.decision` 一直維持 `no-go`。

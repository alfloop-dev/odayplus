# ODP-RELEASE-WORKFLOW-DISPATCH-PARSER-001：Runtime Release 編不起來，所以沒得 dispatch

## 結論

`Runtime Release` 自 2026-08-26T16:11:33Z 起就不是「dispatch 失敗」，而是**整份
workflow 檔編譯不過**。GitHub 編不出 workflow，`workflow_dispatch` 就沒有東西可以
觸發——不是 input 錯、不是權限不足、不是環境沒佈建。

原因只有一行：

```yaml
jobs:
  build:
    env:
      HANDOFF_DIR: ${{ runner.temp }}/runtime-release-handoff   # ← 這裡
```

`runner` context 在 `jobs.<job_id>.env` 不存在。那一層 key 在 runner 還沒配給這個
job 之前就要求值，所以 GitHub 沒有 runner 可以描述。**同一個 expression 往下縮一層
到 `jobs.<job_id>.steps.*` 就完全合法**——`with.path`、`steps.env` 都拿得到
`runner`。這正是它難以在 review 中被看見的原因：寫法看起來是對的，因為在隔壁那一層
它真的是對的。

## 這個缺陷為什麼沒有人發現

GitHub 對「編不出來的 workflow」唯一的訊號是**間接**的：它會在帶進那份壞檔案的
`push` 上建立一個 run，`conclusion: failure`、**job 數 0**、沒有任何 log。

而這份 workflow 的 `on:` 只有 `workflow_dispatch`，**沒有 push trigger**。也就是說：
能編譯的版本在 push 上不會產生任何 run。所以這個缺陷的訊號，是「出現了一個本來不該
存在的 run」——沒有任何東西在監看這件事。倉庫裡也沒有測試在檢查這件事，直到本次。

### 逐字證據：缺陷組

`compile-failure-run-inventory.json`，每一列都是 `gh api` 回讀，另附該 run head 上
`deploy-dev.yml` 的形狀：

| 建立時間 (UTC) | run | 結論 | job 數 | job 層級 `runner.temp` | step 層級 |
|---|---|---|---|---|---|
| 2026-08-26T18:04:21Z | 32997671900 | failure | **0** | 1 | 7 |
| 2026-08-26T17:44:00Z | 32995852711 | failure | **0** | 1 | 7 |
| 2026-08-26T17:19:59Z | 32993493214 | failure | **0** | 1 | 7 |
| 2026-08-26T17:10:22Z | 32992553224 | failure | **0** | 1 | 7 |
| 2026-08-26T16:49:10Z | 32990568816 | failure | **0** | 1 | 4 |
| 2026-08-20T10:36:14Z | 32359736962 | failure | 3 | 0 | 0 |

最後一列是舊的 `Deploy Dev`：當時 `on:` 還有 `push: branches: [dev]`，所以它是一次
真的執行（3 個 job，有 log），失敗原因與本 task 無關。

### 逐字證據：對照組

`compile-control-window.json`。2026-08-25T15:14Z（PR #1010）把 step 層級的
`${{ runner.temp }}` 帶進 `dev`，到 2026-08-26T16:11:33Z（c2ed5e42 加入 job 層級那
一筆）之間：

- `dev` 收到 **11 次 push**（#1011、#1013、#1012、#1020、#1021、#1019、#1022、
  #1024、#1023、#1025、#984 的 merge commit，逐筆列在對照組 JSON 裡）；
- 每一次 push 當下的 `deploy-dev.yml` 都帶著 **3 筆 step 層級**的
  `${{ runner.temp }}`、**0 筆 job 層級**；
- 這段期間 `Deploy Dev` 建立的 run 數：**0**。

step 層級存在、編譯正常、沒有 run。job 層級一加進去，之後每一次 push 都生出一個 0
job 的 failure run。缺陷被隔離到那一行，不是靠推論，是靠這組對照。

## 修正：把 expression 拿掉，不是換一層

可以只把 `HANDOFF_DIR` 移進 step 就修好編譯。本次沒有那樣做，因為那會把同一個
copy-paste 陷阱原封不動留在檔案裡。改成 receipt staging 走 checkout 內的相對路徑，
整份檔案就**一個 `runner.*` expression 都不剩**：

```yaml
env:
  RELEASE_RECEIPT_DIR: .odp_data/release
```

| 收據 | 寫入（`run:`） | 上傳（`with.path`） |
|---|---|---|
| phase precheck | `${RELEASE_RECEIPT_DIR}/release-phase-receipt.json` | `.odp_data/release/release-phase-receipt.json` |
| environment binding × 3 | `${RELEASE_RECEIPT_DIR}/release-environment-receipt.json` | `.odp_data/release/release-environment-receipt.json` |
| admission | `${RELEASE_RECEIPT_DIR}/release-admission-receipt.json` | `.odp_data/release/release-admission-receipt.json` |
| build handoff × 2 | `HANDOFF_DIR=.odp_data/release/runtime-release-handoff` | 同一路徑 |

相對路徑不需要任何 context，`.odp_data/` 已經在 `.gitignore` 裡，三個 receipt writer
都自己 `parent.mkdir(parents=True)`。

### 哪些 `RUNNER_TEMP` 刻意留著

`${RUNNER_TEMP}` 寫在 `run:` 區塊裡時是 **bash 變數**，由 shell 展開，不是 workflow
expression，從來不是這個缺陷的一部分。留著的兩處：

- **簽章 lease 文件**（`${RUNNER_TEMP}/release-lease.json`）。它是憑證不是收據，必須
  待在任何 artifact upload 都碰不到的地方。把它搬進 workspace 是把安全性換成一致性。
- production blue-green 的 dry-run state file，不上傳、不進 manifest。

`tests/ops/test_workflow_expression_contexts.py::test_the_signed_lease_never_lands_inside_the_checkout`
把這個分界寫成契約，避免下一次「統一路徑」把 lease 一起掃進去。

### `.dockerignore` 為什麼在這次的 scope 裡

相對 staging 有一個代價，不處理就會把 build-once 的前提弄壞：

build job 在 `docker build .` **之前**寫 environment binding receipt，而
`api` / `web` / `worker` / `scheduler` 四份 Dockerfile 全部是 `COPY . .`。收據帶
`checked_at` 時戳，留在 build context 裡就代表**同一個 release SHA 每次重跑都會得到
不同的 image digest**——而 Supervisor lease 綁的正是從那些 digest 推出來的
`manifest_digest`。ODP-RELEASE-BUILD-PHASE-BOOTSTRAP-001 建立的「重跑必須逐位元組重現
同一份 handoff」會就此失效。

`.dockerignore` 原本沒有排除 `.odp_data`（該檔開頭寫的是 "Keep build contexts small &
deterministic"，`.odp_data/` 是 runtime 資料樹，本來就不該進 image）。本次補上，並在
`verification-transcript.txt` 對照 B 用真的 `docker build` 證明：有排除時同一個 `COPY`
回報 `"/.odp_data/release/release-phase-receipt.json": not found`；把那一行拿掉，同一個
`COPY` 成功。

## 契約測試

新增 `tests/ops/test_workflow_expression_contexts.py`。它不是針對這一行寫的檢查，而是
把 GitHub 公布的 context-availability 表編碼進去，掃 `.github/workflows/` 下**每一份**
workflow 的每一個 expression：

- expression 用到該 key 拿不到的 context → 失敗，並指名 key、context 與該處允許的集合；
- expression 出現在表格沒有涵蓋的 key 形狀 → 也失敗，訊息要求補上表格那一列，而不是
  略過（否則檢查會安靜地變成 no-op）。

另外四項綁住這次的具體決定：staging root 只宣告一次且是字面相對路徑、寫入端與上傳端
指的是同一組檔案、lease 不得進 workspace、staging root 必須被排除在 image build
context 之外（並斷言「收據先於 `docker build`」這個前提仍然成立——前提若改變，應該
重新推導而不是刪掉排除）。

對照 A 的逐字記錄證明這個測試真的會擋：把 job 層級那一筆放回去，它失敗並印出
`deploy-dev.yml:jobs.build.env.HANDOFF_DIR uses `runner.*`, which GitHub does not
provide to jobs.<job_id>.env`。

## 沒有做的事

- **沒有新增 workflow。**唯一的部署入口仍是 `.github/workflows/deploy-dev.yml`。
- 沒有改 job 順序、environment 綁定、lease admission，或任何收據的內容。
- 沒有改 `check_release_phase.py` / `check_release_environment.py` /
  `check_runtime_admission.py`：它們接的是 `--receipt <path>`，路徑換成相對的之後行為
  完全不變（逐字記錄裡有實際跑過一次）。

## 重新 dispatch build：目前擋在哪裡

本次修正讓 workflow 重新編得起來，但 **build dispatch 還不能從這個 branch 發**：

- `workflow_dispatch` 跑的是所選 ref 上的定義。`dev` 上目前仍是編不起來的版本，要等
  這個 PR 合進 `dev` 之後才有可用的定義。
- default branch 是 `main`，上面仍是統一前的舊 `Deploy Dev`（`on: push: branches:
  [dev]` + 無 input 的 `workflow_dispatch`）。把統一版推上 `main` 是 promote 流程的事，
  不在本 task 範圍。

已經不再是阻礙的：三個 build-scoped environment 現在都存在且已佈建（查詢時間見下），
變數集合與 `check_release_environment.py` 的 `REQUIRED_VARIABLES["build"]` 完全一致，
且沒有 `required_reviewers`——也就是 ODP-RELEASE-BUILD-PHASE-BOOTSTRAP-001 README
「尚待佈建」那一節記的狀況已經解除：

```console
$ gh api repos/alfloop-dev/odayplus/environments -q '.environments[].name'
dev
dev-build
production
production-build
staging
staging-build

$ gh api repos/alfloop-dev/odayplus/environments/dev-build/variables --paginate -q '.variables[].name' | sort | tr '\n' ' '
GCP_AR_REPO GCP_PROJECT_ID GCP_REGION GCP_SERVICE_ACCOUNT GCP_WORKLOAD_IDENTITY_PROVIDER ODP_CLOUD_RUN_API_SERVICE ODP_CLOUD_RUN_SCHEDULER_JOB ODP_CLOUD_RUN_WEB_SERVICE ODP_CLOUD_RUN_WORKER_JOB

$ gh api repos/alfloop-dev/odayplus/environments/dev-build -q '[.protection_rules[].type]'
[]
```

`staging-build` / `production-build` 同樣是 9 個變數、無 protection rule。

## 檔案

| 檔案 | 內容 |
|---|---|
| `compile-failure-run-inventory.json` | 缺陷組：12 個 run 的 metadata 與各自 head 上的 workflow 形狀 |
| `compile-control-window.json` | 對照組：step 層級存在、job 層級不存在的那段期間，11 次 push、0 個 run |
| `verification-transcript.txt` | 測試、lint、相對 staging 實跑，以及對照 A（測試會擋）與對照 B（docker context 排除）|

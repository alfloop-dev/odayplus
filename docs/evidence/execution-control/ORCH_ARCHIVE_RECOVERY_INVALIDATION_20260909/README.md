# Archive Recovery Invalidation & Effective Blocked Resolver

**Task ID**: ORCH-ARCHIVE-RECOVERY-INVALIDATION-001  
**Author**: Antigravity3  
**Reviewer**: Codex  
**Date**: 2026-09-09  

---

## 1. 背景與問題陳述

在先前的歷史還原批次中， 與  因回填規則被判定為完成（）並寫入 。然而後續審查發現：
1.  缺少 Human/Ops 權威決策與對應證據。
2.  缺少 ModelReadyRecord 缺值生產入口驗證。

既有控制面與 resolver 存在以下治理限制：
- Canonical writer 禁止直接對已封存（archived）任務執行 reopen 或原地覆寫。
- 原始 snapshot、index、recovery batch 與 checkpoint 具有不可變（immutable）審計契約，不可任意改寫歷史 bytes。
-  舊版 resolver 僅以 snapshot 的  判定依賴成立，無法撤銷依賴完成語意。

---

## 2. 架構設計與安全邊界

本修補遵循 Pantheon 治理邊界與單一權威來源原則（Single Source of Truth）：

### 2.1 原始封存不可變性（Archive Immutability）
- 嚴禁改寫 、、recovery batch、maintenance hold 或 checkpoint 原始 bytes。
- load_archived_snapshot 保留原始歷史快照與完全一致的 readback。

### 2.2 原子且可追加之更正記錄（Append-Only Per-Task Correction）
- 更正記錄以原子方式寫入 。
- 更正資料結構包含：
  - : 1
  - : 
  - : 目標任務 ID
  - : 原始 snapshot 檔案之 SHA256 雜湊
  - : ISO 8601 UTC 時間戳記
  - : 真實操作者（由  驗證）
  - : 授權協調任務 ID
  - : 撤銷原因
  - : 證據參照路徑
  - : 
  - : 

### 2.3 權限與獨立審查者檢查（Authorization & Reviewer Separation）
- 操作者身份強制透過  獲取，不可透過參數偽造。
- 只有指定的協調任務（coordination task）的**真實 Reviewer** 才能執行撤回。
- 協調任務的 Owner 與 Reviewer 必須獨立（）。
- 若非協調任務 Reviewer 執行，立即拒絕寫入。

### 2.4 窄範圍撤回防護（Narrow-Scope Guardrails）
- **只能撤銷回填任務**：目標 snapshot 必須具備 ，普通歷史封存不可任意撤回。
- **活躍任務防護**：目標任務若已在活躍看板（active board），拒絕執行。
- **快照指紋比對（CAS/Hash Check）**：支援  比對；若磁碟上 snapshot 雜湊不符，拒絕寫入。
- **冪等性（Idempotency）**：相同參數重跑安全返回 ；若存在不同更正內容，拒絕覆蓋。

### 2.5 唯一 Resolver 語意與 Fail-Closed 保證
-  與  是 Supervisor 及 canonical writer 的唯一解析路徑。
- 存在更正檔之任務回傳 effective , , 。
-  對該任務回傳 ， 回傳 。
- **Fail-Closed 原則**：若更正檔存在但格式損壞（Invalid JSON）、ID 不符、缺必要欄位或 snapshot hash 不符，該任務一律判定為 effective ，絕不默默放行或回退為 done。
-  清楚區分 , , , 及原始 。

---

## 3. 操作手冊（Operations Guide）

### 3.1 Dry Run 演練（預設無副作用）


### 3.2 確認寫入更正（--confirm）


### 3.3 狀態檢視與 Readback
{
  "source": "archive",
  "snapshot_path": "ai-task-archive/tasks/ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001.json",
  "snapshot": {
    "version": 1,
    "task_id": "ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001",
    "archived_at": "2026-09-03T17:48:49+00:00",
    "terminal_status": "done",
    "terminal_outcome": "completed",
    "task": {
      "id": "ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001",
      "title": "Merge pull request #1169 from alfloop-dev/task/ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001",
      "phase": "History Recovery",
      "owner": "UNKNOWN-HISTORICAL",
      "reviewer": "UNKNOWN-HISTORICAL",
      "status": "done",
      "terminal_outcome": "completed",
      "depends_on": [],
      "artifacts": [],
      "next": "Reconstructed from verifiable delivery and acceptance evidence.",
      "last_update": "2026-09-03T17:48:49+00:00",
      "history_recovery": {
        "reconstructed": true,
        "record_kind": "reconstructed_done",
        "created_by": "ORCH-ARCHIVE-HISTORY-RECOVERY-001",
        "generated_at": "2026-09-08T23:55:11Z",
        "evidence_tier": "reconstructable_done",
        "gaps": [],
        "historical_actors": {
          "owner": "UNKNOWN-HISTORICAL",
          "reviewer": "UNKNOWN-HISTORICAL",
          "human_go": "UNKNOWN-HISTORICAL"
        },
        "recovery_actors": {
          "owner": "Claude2",
          "reviewer": "Codex"
        },
        "evidence": {
          "local_merge": {
            "merge_commit": "830a8ebf919cd3f9dc3d53fc13c4f76e65eeff6e",
            "merged_at": "2026-09-03T17:48:49+00:00",
            "merge_pr": "#1169",
            "subject": "Merge pull request #1169 from alfloop-dev/task/ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001",
            "delivery_form": "merge-commit"
          },
          "candidates": [
            {
              "pr_number": 1169,
              "url": "https://github.com/alfloop-dev/odayplus/pull/1169",
              "title": "[ReviewBus] ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001 補齊 merge queue 不實作決定的正式可稽核欄位",
              "head_ref": "task/ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001",
              "head_oid": "f2d1bd88ec2dc89148dd3d7346a01de0e921432b",
              "merge_commit": "830a8ebf919cd3f9dc3d53fc13c4f76e65eeff6e",
              "merged_at": "2026-09-03T18:10:43Z",
              "state": "MERGED"
            }
          ],
          "attestations": {
            "acceptance": {
              "source": "docs/evidence/execution-control/ARCHIVE_RECOVERY_EVIDENCE_20260906/task_evidence_inventory.json#entries[task_id=ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001].acceptance_evidence_map (sha256 f4239acfb12d5214709be2ed56beca4d17b278d86942ef11c1f0ea98f007d715)",
              "verified_at": "2026-09-07T15:14:37Z",
              "verifier": "Claude",
              "detail": {
                "criteria_total": 4,
                "by_status": {
                  "met_by_delivered_artifact": 2,
                  "met_by_test_in_green_ci": 2
                },
                "confidence": "high"
              }
            },
            "ci": {
              "source": "gh api repos/alfloop-dev/odayplus/commits/f2d1bd88ec2dc89148dd3d7346a01de0e921432b/check-runs",
              "verified_at": "2026-09-07T15:14:37Z",
              "verifier": "Claude",
              "detail": {
                "head_sha": "f2d1bd88ec2dc89148dd3d7346a01de0e921432b",
                "total": 7,
                "conclusions": {
                  "success": 7
                },
                "checks": [
                  {
                    "name": "product-e2e-gate",
                    "conclusion": "success",
                    "completed_at": "2026-09-03T17:31:05Z"
                  },
                  {
                    "name": "performance-gate",
                    "conclusion": "success",
                    "completed_at": "2026-09-03T17:27:22Z"
                  },
                  {
                    "name": "product",
                    "conclusion": "success",
                    "completed_at": "2026-09-03T17:47:27Z"
                  },
                  {
                    "name": "classify",
                    "conclusion": "success",
                    "completed_at": "2026-09-03T17:25:46Z"
                  },
                  {
                    "name": "orchestrator",
                    "conclusion": "success",
                    "completed_at": "2026-09-03T17:28:13Z"
                  },
                  {
                    "name": "change-scope",
                    "conclusion": "success",
                    "completed_at": "2026-09-03T17:26:08Z"
                  },
                  {
                    "name": "boundary",
                    "conclusion": "success",
                    "completed_at": "2026-09-03T17:25:35Z"
                  }
                ]
              }
            },
            "runtime": {
              "source": "acceptance_class(runtime_deployment=false, external_enablement=false, human_authority=false) @ docs/evidence/execution-control/ARCHIVE_RECOVERY_EVIDENCE_20260906/task_evidence_inventory.json (sha256 f4239acfb12d5214709be2ed56beca4d17b278d86942ef11c1f0ea98f007d715); find_merge_evidence on origin/dev 19167c10b8c6978fb4aea934bc6a668dc926b74e -> merge 830a8ebf919cd3f9dc3d53fc13c4f76e65eeff6e (merge-commit, #1169); live runtime oday-plus-supervisor-runtime-19167c10b8c6",
              "verified_at": "2026-09-07T15:14:37Z",
              "verifier": "Claude",
              "detail": {
                "acceptance_class": {
                  "code_or_docs": true,
                  "runtime_deployment": false,
                  "external_enablement": false,
                  "human_authority": false
                },
                "local_merge": {
                  "merge_commit": "830a8ebf919cd3f9dc3d53fc13c4f76e65eeff6e",
                  "merged_at": "2026-09-03T17:48:49+00:00",
                  "merge_pr": "#1169",
                  "subject": "Merge pull request #1169 from alfloop-dev/task/ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001",
                  "delivery_form": "merge-commit"
                },
                "meaning": "本任務驗收不含 runtime 部署；交付碼在 pinned ref 與 live runtime 內。"
              }
            },
            "approval": {
              "source": "gh api repos/alfloop-dev/odayplus/commits/f2d1bd88ec2dc89148dd3d7346a01de0e921432b/status (context=task-review-gate)",
              "verified_at": "2026-09-07T15:14:37Z",
              "verifier": "Claude",
              "detail": {
                "head_sha": "f2d1bd88ec2dc89148dd3d7346a01de0e921432b",
                "state": "success",
                "updated_at": "2026-09-03T17:44:08Z",
                "description": "Approved by assigned reviewer Antigravity4",
                "approver_named_in_gate": "Antigravity4",
                "approver_differs_from_owner": true
              }
            },
            "_disclosure": {
              "attested": [
                "acceptance",
                "approval",
                "ci",
                "runtime"
              ],
              "withheld": [],
              "acceptance_class": {
                "code_or_docs": true,
                "runtime_deployment": false,
                "external_enablement": false,
                "human_authority": false
              },
              "merged_inventory_confidence": "high",
              "skipped_checks_at_head": 0,
              "process_constraint_unverifiable_criteria": 0,
              "partially_met_criteria": 0,
              "merged_inventory_gaps": [
                "盤點觀察：Acceptance『找不到權威決策時 task 轉 blocked waiting Human/Ops』的負向行為未在本盤點重跑驗證。",
                "第 4 輪更正（結論不變）：A3-A4 的 delivery_toolchain/governance/test_check_requirement_members.py 同上 kind 正規化與收集依據。"
              ],
              "candidate_mapping_verdict": "consistent",
              "local_merge_verified_on_pinned_ref": true
            }
          },
          "verified_at": "2026-09-08T23:55:11Z",
          "verifier": "Claude2"
        },
        "known_dependents": [
          {
            "id": "ODP-STRUCTURAL-REMEDIATION-CLOSEOUT-001",
            "status": "unknown-at-rebuild-time"
          }
        ],
        "baseline": {
          "ref": "origin/dev",
          "ref_commit": "ef76cf6d295ce7a8a470fe6e5f0eab20a0439169",
          "inventory_sha256": "54d2386b29a9b601d13e74e55a2f1702550421546c1e992de295c51fe36d12ca",
          "authorization_sha256": "d7a34fa1fbed837d9f15d2580709689b1178f11f2d1f019383082eebf11deea3"
        },
        "note": "重建記錄，來源為 PR/commit 等可驗證證據，不是原始 archive bytes，也不代表原始驗收、CI、部署或核准已完成。"
      }
    },
    "handoffs": [],
    "blockers": []
  }
}

---

## 4. 回退與版本降級風險警告（Rollback Risk Warning）

> [!WARNING]
> **關鍵回退風險（Rollback Risk）**：
> 舊版 Runtime（本修補合併前之程式碼）並不具備讀取  的能力。
> 若在套用 invalidation 更正後將 Supervisor 或 ai-status 程式碼降版至未修補版本，舊版 resolver 將會忽略更正記錄，直接從原始 snapshot 讀取 ，導致依賴完成語意被錯誤放行。
> **因此：一旦更正記錄落地，嚴禁未經協調直接將控制面 runtime 降版。**

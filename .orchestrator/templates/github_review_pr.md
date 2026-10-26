{{marker}}
# Pantheon Review Bus

## 任務資訊
- 任務 ID: `{{task_id}}`
- 標題: {{task_title}}
- 摘要: {{task_summary}}
- 狀態: `{{task_status}}`
- 負責人: `{{task_owner}}`
- 評審人: `{{task_reviewer}}`
- 依賴任務: {{depends_on}}

## 審查範圍
{{artifacts}}

## 分支資訊
- 來源分支: `{{branch}}`
- 目標分支: `{{base_branch}}`

## 下一步
{{next_step}}

## 行動審查指南
請使用 GitHub Mobile PR 審查動作：
- `Approve`
- `Request changes`
- `Comment`

Orchestrator 會輪詢審查結果並自動同步回寫至 `ai-status.json`。
<!-- /pantheon-bus -->

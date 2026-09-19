ODP Execution Tasks（已登記的五筆承接）

此表對應已提交 canonical board 的任務，不是另外新增待派工清單。完整 acceptance／verification／scope 見 [execution-tasks-registered.json](execution-tasks-registered.json)。狀態以各觀測檔时间為準。

| Task | Owner | Reviewer | Repository | Dependencies |
|---|---|---|---|---|
| `DPF-CAPTURE-RETENTION-RUNNER-001` | Antigravity5 | Codex | alfloop-dev/oday-data-platform | `DPF-ACQUISITION-RETENTION-BRIDGE-001`、`DPF-SITE-CONTEXT-REAL-COMPONENTS-001` |
| `DPF-BOUNDED-CAPTURE-RETENTION-EXECUTION-001` | Antigravity6 | Codex | alfloop-dev/oday-data-platform | `DPF-CAPTURE-RETENTION-RUNNER-001` |
| `ODP-DEV-RELEASE-GATE-RECONCILIATION-004` | Antigravity7 | Codex | alfloop-dev/odayplus | `ODP-STAGING-FOUNDATION-IAC-REMEDIATION-001`、`ODP-GITHUB-GCP-ENV-BOOTSTRAP-001` |
| `ODP-STAGING-RECOVERY-STORAGE-ACCEPTANCE-001` | Antigravity6 | Codex | alfloop-dev/odayplus |  |
| `ODP-BRAND-CLASSIFICATION-SYNC-PRESERVATION-001` | Antigravity7 | Codex | alfloop-dev/odayplus |  |

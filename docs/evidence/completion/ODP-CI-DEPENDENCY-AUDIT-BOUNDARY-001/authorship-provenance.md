## 執行者與歷史作者 metadata

此修復由 Codex 前景工作階段執行；獨立 approval 以 canonical reviewer 與 exact head 的核准收據為準。2026-09-06 核對發現共用 `/home/lupin/odayplus/.git/config` 的 `user.name` / `user.email` 設為 `Claude` / `claude@pantheon.local`，因此部分先前由 Codex 執行的修復 commit 的 Git author / committer metadata 沿用了 Claude；這些 commit 的 `LLM-Agent: Codex` trailer 記錄了實際執行者。這不是 Claude review 或 Human/Ops approval 的證據。

已核准 head 的歷史保持原樣，不改寫 author 或 commit。從本次 PR #1188 最終整合起，Codex 透過單次命令的 `GIT_AUTHOR_*` / `GIT_COMMITTER_*` 明確指定自己的身分，避免修改會影響其他 worker 的共享 Git 設定。漏洞豁免仍一律禁止。

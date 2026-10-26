# PR #1188 最終整合驗證

實際程式／lock／gate source：`9b64631dfea6390ab0e665eab58e18d47f2a72ba`。此整合 commit 的第二個 parent 為已合併的 `dev` `64f3b2399442e8cd7531284d1e7a3e33bcc3ca9a`，包含 PR #1222 的 NLTK／Evidently 移除與原生監控切換。唯一衝突為產生式 code-boundary inventory，已重新產生；與該 dev 的程式差異維持原本八個 task 檔案。隨後的 evidence commit 僅加入本目錄，不改 gate、程式或依賴。

## 實際完整依賴掃描

- 時間：2026-09-06T05:58:29.293312+00:00 至 2026-09-06T05:58:58.405333+00:00。
- 工具：`pip-audit 2.10.1`；advisory source：PyPI。
- 透過此 source 內原封不動的 `pip_audit_gate.audit_with_retry` 執行真實 subprocess，明確指向本 candidate 的 `.venv/lib/python3.12/site-packages`；完整 CLI、原始 stdout/stderr 與 SHA-256 存於 `repaired-audit.json` 及其引用檔。
- 實際 installed 與 audited 的正規化套件名稱／版本集合完全相同：215 個，零 skip、零 finding，exit 0。Evidently 與 NLTK 均不在此安裝／掃描集合。
- 此次記錄使用一次實際 scan；沒有 `--ignore-vuln`、waiver、假版本、`--no-deps` 或將套件搬到未掃描範圍。fixture 回歸測試不作為這次真實掃描的替代證據。

## 局部回歸

`.venv/bin/python -m pytest tests/tooling/test_dependency_audit_boundary.py tests/security/test_supply_chain_security_gate.py .orchestrator/test_source_document_router.py -q`：112 passed，exit 0。涵蓋 no-suppression、拒絕 ignore CLI、ignore 環境變數無法放行真 finding、空／不完整報告 fail closed、有限參數與 transient-only retry、唯一 audit 接線、SBOM component 一致性，以及被此 PR 修改的 source-router fixture。原始輸出與命令／時間保留於 `repaired-focused-tests.txt`、`repaired-focused-command.json`。

`uv sync --frozen --python 3.12` 移除了 resolver 已刪除的 23 個套件。`check_code_boundaries.py --write-inventory`／check 通過（1136 files）；相對最新 dev 的 `git diff --check` 通過。本地不再重跑無關完整 suite，完整 required CI 由新 PR head 執行。

## 歷史與剩餘交付

`6d82eb1ba5858daee55f6da7f7be53d393972c05` 的 waiver-enabled PASS 不代表漏洞修復，也不是有效的 Human/Ops approval。該 commit 仍可在此 PR 的歷史中追溯；最終 tree 已移除豁免檔、loader、ignore CLI 與 `ODP_PIP_AUDIT_IGNORE_VULNS` 入口。保留真實歷史，不以改寫歷史遮蔽事件。Git 作者 metadata 的已知差異與修正方式詳見 `authorship-provenance.md`。

`repaired-inputs.json` 保存 source／lock／SBOM／本目錄證據 hash，以及當時從 GitHub 讀取的前置 PR 真實 merge 收據；其中 #1188 的 OPEN 狀態是本次重新提交前的快照。此紀錄不冒充新 head 的 required CI、獨立 approval 或 merge 結果：這些仍須正常完成，最終修復鏈另由 ODP-DRIFT-SECURITY-VERIFY-003 核對。未部署，未授予 production GO。

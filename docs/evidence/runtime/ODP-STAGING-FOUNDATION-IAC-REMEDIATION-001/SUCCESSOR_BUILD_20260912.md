# 2026-09-12 真實 successor build

唯一 Runtime Release [run 34726258529](https://github.com/alfloop-dev/odayplus/actions/runs/34726258529) 已成功，candidate 為 `b1e9b57b0b61bcc692ec16b2d7a0ce03b612fd8e`，workflow ref dev 為 `36a102b7b39d1fe2e58939ee6e454e20c6d72dbd`。此 candidate 包含 foundation 與已審查依賴修復；尚未包含獨立 SQL PR #1323。

- 真實 production npm receipt：所有 severity 均為 0，原有漏洞門檻保留。
- Secret/SAST、SBOM、locked dependencies、部署 health/backup/restore/rollback 測試、WIF 與 initial target absence readback 成功。
- Build job 完成四個 immutable images 的 publish、Cosign sign/verify、SBOM attestation，並產生 manifest 與 image handoff。
- 已下載全部六份原始 artifact ZIP，逐份比對 GitHub API archive digest；解壓後 bytes 原樣保存本機。四個 handoff image refs 與 manifest 完全一致。
- 使用此 candidate 的原 validator 驗證下載 manifest：integrity 與 artifact admissibility errors 都為空。Manifest digest 為 `sha256:5a203c4d215085bfd87219f57bcd3c862025b5531e2aceddeda48a6ab47d8d56`；raw JSON SHA-256 為 `dc8f1a4730436740be8b35b999c37903b063cf65fed05b705896e6aaf10d5dc7`。

本提交只增加驗證摘要，沒有更換 canonical manifest、修改 NO-GO registry、完成獨立 review gate 或部署服務。舊 canonical manifest 的四項契約不一致仍須正式 candidate reconciliation；新 artifact 本身通過驗證不會自動改變 deployment admission。SQL #1323 若整合後改變 code/build inputs，必須用新 candidate 重新建置，不能把本次 digests 宣稱綁定另一個 SHA。

原始詳細 artifact 保留本機 operational handoff；公開的 artifact IDs、ZIP digest、解壓檔案 SHA-256 與驗證結果見 `SUCCESSOR_BUILD_VALIDATION_20260912.json`。原始收據不會手動改寫。

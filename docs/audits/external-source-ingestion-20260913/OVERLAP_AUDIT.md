# 已派工、PR、既有實作與新規劃重疊稽核

核對時間與exact heads見overlap-audit.json。這不是只比task名稱。

## 實際正在執行

讀取Supervisor runtime state、worker status及/proc，當次只有Nullable cutover owner worker正在執行：ODP-CANONICAL-MEASUREMENT-NULLABLE-CUTOVER-001，位於odayplus工作樹。新規劃是oday-data-platform repo，沒有與該writer共用修改路徑。

根對話的MOF/RIS產品修改已停止並封存；其隔離工作樹尚有未提交差異，不允許Supervisor直接復用。由同一既有task匯入到worker自己的乾淨工作樹，避免再建一筆重複MOF/RIS實作。

## 新規劃中的真實重疊

MOF/RIS task與後續official task共用4檔：defs/external/mof_moi.py、defs/external/ris_nlsc.py、tests/sources/test_official_live_acquisition.py、tests/sources/official_source_fixtures.py。已設official depends_on MOF/RIS，因此必須先完成前者再派後者，不可同時當writer。

其餘新規劃task沒有owned_paths互相重疊。現有masked #63及activation所擁有的release、runtime、policy/configmap已列入新接入task禁止修改範圍。

## 現有PR實際檔案

- data-platform #63（head 56c9c2bb…）：masked materializer、artifact evidence、runtime/workflow等；本次新接入任務不改这些檔案，沿用#63。
- data-platform #1（head 0577773d…）：transactions/raw_transactions/schedules/dev configmap與tests；與本次external domain接入無直接檔案重疊。不是因沒有列入新任務就視為完成。
- odayplus #1305已MERGED，canonical仍in_progress；這是已合併功能等待收尾，不能按task狀態再開另一個quota recovery實作。
- odayplus #1325、#1326、#1327仍OPEN；與新data-platform來源接入不同repo。#1325/#1326同時宣告code-boundary-inventory等共用面，且quota fence與已合併quota recovery有共同orchestrator範圍，需沿用既有任務review/closeout，不增開相同修復。

## 功能去重

已合併的來源adapter、kernel、retention bootstrap、SiteMarketContextService均當作可重用底座。本次新增範圍是「production入口接線、真實endpoint/format、來源readback與跨階段binding」，不是重新做adapter或重建資料平台。

masked snapshot、source activation、XR audit、OSS legal gate沿用既有ID；新增7筆加接手1筆的規劃不包含這四筆替代任務。

## 限制與派工前條件

部分舊task沒有owned_paths，不能因此宣稱全系統永遠沒有重疊。本次以實際running worker和相關PR檔案補查；Supervisor每次dispatch仍須用新的HEAD/lease/worktree再核對。

新任務只有設計基線先可派。其他依賴設計/共用底座；任何path overlap若缺序列依賴、或相同task另有live writer，一律不啟動第二個writer。

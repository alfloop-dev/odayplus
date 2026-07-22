"use client";

import { useMemo, useState } from "react";
import type { AssistedIntake, MatchOutcome } from "@oday-plus/openapi-client";
import styles from "./intake.module.css";
import { IntakeDialogShell } from "./IntakeDialogShell";
import type { IntakeApiError } from "./intakeClient";
import { ListingCompareTable, type TargetListingData } from "./ListingCompareTable";
import { MatchEvidencePanel } from "./MatchEvidencePanel";
import {
  decisionTitle,
  matchLabel,
  matchTone,
  shortUrl,
  type IntakeDecisionKind,
} from "./intakeTypes";

export type IdentityGraphMode = "merge" | "split" | "unmerge" | "reversal";

export type IdentityDecisionResultReceipt = {
  receiptId: string;
  actor: string;
  actorRole: string;
  timestamp: string;
  intakeId: string;
  targetListingId: string;
  decisionKind: IntakeDecisionKind;
  graphMode: IdentityGraphMode;
  beforeVersion: string;
  afterVersion: string;
  correlationId: string;
  auditEventId: string;
};

export function IdentityDecisionPanel({
  record,
  currentOperator = { id: "OP-101", name: "Current Operator", role: "operations_manager" },
  targetListing,
  busy = false,
  error = null,
  proposerId: customProposerId,
  reviewerId: customReviewerId,
  requireSecondActor = true,
  onSubmitDecision,
  onRefresh,
  className,
}: {
  record: AssistedIntake;
  currentOperator?: { id: string; name: string; role: string };
  targetListing?: TargetListingData | null;
  busy?: boolean;
  error?: IntakeApiError | null;
  proposerId?: string;
  reviewerId?: string;
  requireSecondActor?: boolean;
  onSubmitDecision?: (input: {
    kind: IntakeDecisionKind;
    graphMode: IdentityGraphMode;
    reason: string;
    riskSummary: string;
    riskAcknowledged: boolean;
    proposerId: string;
    reviewerId: string;
    ifMatchVersion?: string;
  }) => Promise<IdentityDecisionResultReceipt | void> | void;
  onRefresh?: () => void;
  className?: string;
}) {
  const match = record.matchResult;
  const outcome: MatchOutcome = match?.outcome ?? "POSSIBLE_MATCH";
  const targetId = targetListing?.id || match?.targetListingId || "";

  // Proposer and Reviewer setup for 2nd actor governance
  const proposerId = customProposerId || record.submitter || "OP-100";
  const reviewerId = customReviewerId || currentOperator.id;

  // Self-review denial check: Proposer and Reviewer cannot be the same person when second actor review is required
  const isSelfReviewDenied = requireSecondActor && proposerId === reviewerId;

  // Graph action mode state
  const [graphMode, setGraphMode] = useState<IdentityGraphMode>(() => {
    if (outcome === "REVISION") return "merge";
    if (outcome === "EXACT_DUPLICATE") return "merge";
    return "merge";
  });

  // Decision kind state
  const [decisionKind, setDecisionKind] = useState<IntakeDecisionKind>(() => {
    if (outcome === "REVISION") return "revise";
    if (outcome === "EXACT_DUPLICATE") return "dup";
    if (outcome === "NEW") return "create";
    return "create";
  });

  // Inputs
  const [reason, setReason] = useState("");
  const [riskAcknowledged, setRiskAcknowledged] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);

  // Concurrency conflict state & preserved input
  const [conflictState, setConflictState] = useState<{
    hasConflict: boolean;
    currentVersion?: string;
    currentOwner?: string;
  } | null>(() => {
    if (error?.code === "ODP-INTAKE-CONFLICT" || error?.status === 409) {
      return {
        hasConflict: true,
        currentVersion: "v2-updated",
        currentOwner: record.owner || "Other Operator",
      };
    }
    return null;
  });

  // Receipt state upon success
  const [receipt, setReceipt] = useState<IdentityDecisionResultReceipt | null>(null);

  // Tab mode within panel: "summary" | "compare" | "graph"
  const [activeTab, setActiveTab] = useState<"summary" | "compare" | "graph">("summary");

  // Dynamic Risk Summary
  const riskSummaryText = useMemo(() => {
    const evidence = `快照 ${record.snapshotId ?? "—"} · ${record.parserVersion}`;
    switch (graphMode) {
      case "merge":
        return (
          `[合併模式 Graph Merge] 將收件 ${record.id} 的內容併入標的物件 ${targetId || "（未指定）"} 並建立新版本；` +
          `舊版本將被保留，歷史圖譜可追溯。依據：${evidence}。`
        );
      case "split":
        return (
          `[拆分模式 Graph Split] 將收件 ${record.id} 從標的物件 ${targetId || "（未指定）"} 拆分為全新獨立 Listing；` +
          `將解除兩者間的關聯圖譜。依據：${evidence}。`
        );
      case "unmerge":
        return (
          `[反轉合併 Graph Unmerge] 解鎖並撤銷之前對 ${targetId || "（未指定）"} 的合併圖譜關係；` +
          `恢復為獨立收件節點。依據：${evidence}。`
        );
      case "reversal":
        return (
          `[歷程回滾 Graph Reversal] 回滾 ${targetId || "（未指定）"} 的識別變更歷史至上一穩定版本。` +
          `依據：${evidence}。`
        );
      default:
        return `將對收件 ${record.id} 執行身份圖譜變更。依據：${evidence}。`;
    }
  }, [graphMode, record.id, record.snapshotId, record.parserVersion, targetId]);

  // Graph Plan lineage node state before and after
  const graphPlan = useMemo(() => {
    const beforeNodes = [
      { id: record.id, type: "IntakeSubmission", label: `收件: ${shortUrl(record.canonicalUrl, 20)}` },
      ...(targetId ? [{ id: targetId, type: "TargetListing", label: `既有物件 ${targetId}` }] : []),
    ];

    let afterNodes: Array<{ id: string; type: string; label: string }> = [];
    if (graphMode === "merge") {
      afterNodes = [
        {
          id: targetId || `LST-NEW-${record.id.slice(-4)}`,
          type: "TargetListing (v2)",
          label: `${targetId || "LST-NEW"} (合併新版本 v2)`,
        },
      ];
    } else if (graphMode === "split") {
      afterNodes = [
        { id: targetId || "LST-ORIGINAL", type: "TargetListing", label: `${targetId || "既有物件"} (獨立)` },
        { id: `LST-NEW-${record.id.slice(-4)}`, type: "NewListing", label: `LST-NEW-${record.id.slice(-4)} (獨立新物件)` },
      ];
    } else if (graphMode === "unmerge") {
      afterNodes = [
        { id: targetId || "LST-ORIGINAL", type: "TargetListing", label: `${targetId || "原物件"} (已解除合併)` },
        { id: record.id, type: "UnmergedIntake", label: `${record.id} (獨立收件節點)` },
      ];
    } else {
      afterNodes = [
        { id: targetId || "LST-ORIGINAL", type: "TargetListing (Rolled back)", label: `${targetId || "既有物件"} (已回滾至 v1)` },
      ];
    }

    return { beforeNodes, afterNodes };
  }, [graphMode, record.id, record.canonicalUrl, targetId]);

  async function handleSubmit() {
    if (busy) return;
    if (isSelfReviewDenied) {
      setLocalError("提案者與審查者不能為同一人，無法提交此決策 (SELF_REVIEW_DENIED)。");
      return;
    }
    if (!reason.trim()) {
      setLocalError("請填寫決策原因（寫入 Audit 與身份圖譜歷程）。");
      return;
    }
    if (!riskAcknowledged) {
      setLocalError("請勾選風險確認，聲明你已了解圖譜變更影響。");
      return;
    }

    setLocalError(null);
    try {
      const res = await onSubmitDecision?.({
        kind: decisionKind,
        graphMode,
        reason: reason.trim(),
        riskSummary: riskSummaryText,
        riskAcknowledged,
        proposerId,
        reviewerId,
        ifMatchVersion: conflictState?.currentVersion || "v1",
      });

      if (res && res.receiptId) {
        setReceipt(res);
      } else {
        // Fallback receipt mock for demonstration when onSubmitDecision returns void
        setReceipt({
          receiptId: `RCPT-MATCH-${Date.now().toString(36).toUpperCase()}`,
          actor: currentOperator.name,
          actorRole: currentOperator.role,
          timestamp: new Date().toISOString(),
          intakeId: record.id,
          targetListingId: targetId || "LST-AUTO",
          decisionKind,
          graphMode,
          beforeVersion: "v1",
          afterVersion: "v2",
          correlationId: record.correlationId || "corr-default",
          auditEventId: `AUDIT-${Date.now()}`,
        });
      }
    } catch (err: unknown) {
      if (typeof err === "object" && err !== null && "status" in err && (err as { status: number }).status === 409) {
        setConflictState({
          hasConflict: true,
          currentVersion: "v2-updated",
          currentOwner: record.owner || "Another Operator",
        });
        setLocalError("偵測到版本衝突 (409 OWNER_CONFLICT)！其他人員已更新該筆資料，你的輸入已為你完整保留。");
      } else {
        setLocalError((err as Error)?.message || "提交決策時發生錯誤，請重試。");
      }
    }
  }

  function handleRefreshAndRetry() {
    onRefresh?.();
    setConflictState(null);
    setLocalError(null);
  }

  const shownError = localError || error?.summary || null;

  return (
    <div
      aria-label={`可逆身份決策面板 ${record.id}`}
      className={`${styles.sectionBox} ${className || ""}`}
      data-testid="identity-decision-panel"
      role="region"
    >
      {/* Header */}
      <div className={styles.sectionHead}>
        <span>可逆身份圖譜審查與決策 IDENTITY & REVERSIBLE GRAPH REVIEW</span>
        <span className={styles.chip} data-testid="identity-match-badge" data-tone={matchTone(outcome)}>
          {outcome} · {matchLabel(outcome)}
        </span>
        <span className={styles.rowId}>{record.id}</span>
      </div>

      {/* Navigation tabs */}
      <div className={styles.counts} style={{ margin: "10px 0", borderBottom: "1px solid #eef1f6", paddingBottom: "6px" }}>
        <button
          className={activeTab === "summary" ? styles.primaryButton : styles.secondaryButton}
          data-testid="tab-summary-btn"
          onClick={() => setActiveTab("summary")}
          type="button"
          style={{ padding: "4px 12px", fontSize: "11px" }}
        >
          1. 審查與授權 Summary & Auth
        </button>
        <button
          className={activeTab === "compare" ? styles.primaryButton : styles.secondaryButton}
          data-testid="tab-compare-btn"
          onClick={() => setActiveTab("compare")}
          type="button"
          style={{ padding: "4px 12px", fontSize: "11px" }}
        >
          2. 欄位差異 Compare Table
        </button>
        <button
          className={activeTab === "graph" ? styles.primaryButton : styles.secondaryButton}
          data-testid="tab-graph-btn"
          onClick={() => setActiveTab("graph")}
          type="button"
          style={{ padding: "4px 12px", fontSize: "11px" }}
        >
          3. 可逆圖譜計畫 Graph Plan
        </button>
      </div>

      {/* Screen-reader summary */}
      <div className={styles.srSummary} data-testid="identity-sr-summary" role="region" aria-live="polite">
        身份審查狀態：{outcome}，提案者：{proposerId}，審查者：{reviewerId}。
        {isSelfReviewDenied ? "警告：自我審查已被拒絕 (SELF_REVIEW_DENIED)。" : "雙人授權查核正常。"}
      </div>

      {/* Receipt View upon successful decision */}
      {receipt ? (
        <div className={styles.sectionBox} data-testid="identity-durable-receipt" style={{ background: "#f0fdf4", borderColor: "#bbf7d0" }}>
          <div className={styles.sectionHead} style={{ color: "#166534" }}>
            ✓ 決策憑證已生成 DURABLE RECEIPT
            <span className={styles.chip} data-tone="good">
              {receipt.receiptId}
            </span>
          </div>
          <div className={styles.metaGrid}>
            <div>
              <span className={styles.metaCaption}>憑證編號 Receipt ID</span>
              <div className={styles.metaValue} data-testid="receipt-id-val">
                <code>{receipt.receiptId}</code>
              </div>
            </div>
            <div>
              <span className={styles.metaCaption}>執行人員 Actor</span>
              <div className={styles.metaValue} data-testid="receipt-actor-val">
                {receipt.actor} ({receipt.actorRole})
              </div>
            </div>
            <div>
              <span className={styles.metaCaption}>圖譜模式 & 動作</span>
              <div className={styles.metaValue} data-testid="receipt-action-val">
                {receipt.graphMode} / {decisionTitle(receipt.decisionKind, record)}
              </div>
            </div>
            <div>
              <span className={styles.metaCaption}>版本演進 Version</span>
              <div className={styles.metaValue} data-testid="receipt-version-val">
                {receipt.beforeVersion} → {receipt.afterVersion}
              </div>
            </div>
            <div>
              <span className={styles.metaCaption}>Audit ID</span>
              <div className={styles.metaValue} data-testid="receipt-audit-val">
                {receipt.auditEventId}
              </div>
            </div>
            <div>
              <span className={styles.metaCaption}>時間戳記 Timestamp</span>
              <div className={styles.metaValue} data-testid="receipt-time-val">
                {receipt.timestamp}
              </div>
            </div>
          </div>
          <div style={{ marginTop: "10px", textAlign: "right" }}>
            <button
              className={styles.secondaryButton}
              data-testid="receipt-close-btn"
              onClick={() => setReceipt(null)}
              type="button"
            >
              關閉憑證頁面，回到審查
            </button>
          </div>
        </div>
      ) : null}

      {/* Tab 1: Summary & Auth */}
      {activeTab === "summary" ? (
        <>
          <MatchEvidencePanel record={record} />

          {/* Dual-actor Proposer/Reviewer Authorization Box */}
          <div className={styles.sectionBox} data-testid="identity-auth-box" style={{ marginTop: "12px" }}>
            <div className={styles.sectionHead}>
              雙人授權與核准層級 DUAL-ACTOR AUTHORIZATION
              {isSelfReviewDenied ? (
                <span className={styles.chip} data-tone="risk" data-testid="self-review-denied">
                  ✕ SELF_REVIEW_DENIED
                </span>
              ) : (
                <span className={styles.chip} data-tone="good" data-testid="self-review-passed">
                  ✓ 雙人角色驗證通過
                </span>
              )}
            </div>

            <div className={styles.metaGrid}>
              <div>
                <span className={styles.metaCaption}>提案者 Proposer</span>
                <div className={styles.metaValue} data-testid="proposer-id-val">
                  <code>{proposerId}</code>
                </div>
              </div>
              <div>
                <span className={styles.metaCaption}>當前審查者 Reviewer</span>
                <div className={styles.metaValue} data-testid="reviewer-id-val">
                  <code>{reviewerId}</code> ({currentOperator.role})
                </div>
              </div>
              <div>
                <span className={styles.metaCaption}>二級覆核要求</span>
                <div className={styles.metaValue}>
                  {requireSecondActor ? "強制要求 (Second-Actor Required)" : "單人審查許可"}
                </div>
              </div>
            </div>

            {isSelfReviewDenied ? (
              <div className={styles.errorPanel} data-testid="self-review-denied-notice" style={{ marginTop: "8px" }}>
                <span className={styles.errorSummary}>✕ 自我審查已拒絕 (SELF_REVIEW_DENIED)</span>
                <span className={styles.errorMeta}>
                  系統安全規範：案件提案者與最終審查者不能為同一人 ({proposerId})。請由第二位審查人員代為核准此決策。
                </span>
              </div>
            ) : null}
          </div>
        </>
      ) : null}

      {/* Tab 2: Compare Table */}
      {activeTab === "compare" ? (
        <ListingCompareTable record={record} targetListing={targetListing} />
      ) : null}

      {/* Tab 3: Reversible Graph Plan */}
      {activeTab === "graph" || activeTab === "summary" ? (
        <div className={styles.sectionBox} data-testid="identity-graph-plan-box" style={{ marginTop: "12px" }}>
          <div className={styles.sectionHead}>
            可逆圖譜變更計畫 REVERSIBLE GRAPH PLAN
            <span className={styles.sectionHeadHint}>選擇圖譜操作模式：</span>
          </div>

          {/* Graph mode selection buttons */}
          <div className={styles.actionRow} data-testid="graph-mode-selector" style={{ marginBottom: "12px" }}>
            <button
              className={graphMode === "merge" ? styles.primaryButton : styles.secondaryButton}
              data-testid="graph-mode-merge"
              onClick={() => {
                setGraphMode("merge");
                setDecisionKind("revise");
              }}
              type="button"
            >
              1. 合併模式 (Merge / Revise)
            </button>
            <button
              className={graphMode === "split" ? styles.primaryButton : styles.secondaryButton}
              data-testid="graph-mode-split"
              onClick={() => {
                setGraphMode("split");
                setDecisionKind("create");
              }}
              type="button"
            >
              2. 拆分模式 (Split)
            </button>
            <button
              className={graphMode === "unmerge" ? styles.primaryButton : styles.secondaryButton}
              data-testid="graph-mode-unmerge"
              onClick={() => {
                setGraphMode("unmerge");
                setDecisionKind("steward");
              }}
              type="button"
            >
              3. 解除合併 (Unmerge)
            </button>
            <button
              className={graphMode === "reversal" ? styles.primaryButton : styles.secondaryButton}
              data-testid="graph-mode-reversal"
              onClick={() => {
                setGraphMode("reversal");
                setDecisionKind("steward");
              }}
              type="button"
            >
              4. 歷程回滾 (Reversal)
            </button>
          </div>

          {/* Graph Lineage Impact Display */}
          <div className={styles.signals} data-testid="graph-lineage-impact">
            <div className={styles.signalCol} data-testid="graph-before-nodes">
              <div className={styles.signalHeadCon}>變更前節點 Lineage Before</div>
              {graphPlan.beforeNodes.map((node) => (
                <div className={styles.signalItem} key={node.id} data-testid={`node-before-${node.id}`}>
                  <code>[{node.type}]</code> <strong>{node.id}</strong> — {node.label}
                </div>
              ))}
            </div>
            <div className={styles.signalCol} data-testid="graph-after-nodes">
              <div className={styles.signalHeadAgree}>變更後預期節點 Lineage After</div>
              {graphPlan.afterNodes.map((node) => (
                <div className={styles.signalItem} key={node.id} data-testid={`node-after-${node.id}`}>
                  <code>[{node.type}]</code> <strong>{node.id}</strong> — {node.label}
                </div>
              ))}
            </div>
          </div>
        </div>
      ) : null}

      {/* Decision Submission Form Section */}
      <div className={styles.sectionBox} style={{ marginTop: "12px" }}>
        <div className={styles.sectionHead}>身份審查決策提交 IDENTITY DECISION SUBMIT</div>

        {/* Possible Match Notice */}
        {outcome === "POSSIBLE_MATCH" ? (
          <div className={styles.warnNote} data-testid="identity-no-auto-merge-note">
            此案為 <code>POSSIBLE_MATCH</code>（疑似重複）。系統不會自動合併，請手動勾選決策並輸入核准原因。
          </div>
        ) : null}

        {/* Decision Kind Selection Buttons */}
        <div className={styles.actionRow} data-testid="identity-decision-kinds" style={{ marginBottom: "12px" }}>
          <button
            className={decisionKind === "create" ? styles.primaryButton : styles.secondaryButton}
            data-testid="btn-decision-create"
            disabled={isSelfReviewDenied || busy}
            onClick={() => setDecisionKind("create")}
            type="button"
          >
            建立新物件 (Create)
          </button>
          <button
            className={decisionKind === "revise" ? styles.primaryButton : styles.secondaryButton}
            data-testid="btn-decision-revise"
            disabled={isSelfReviewDenied || busy}
            onClick={() => setDecisionKind("revise")}
            type="button"
          >
            加入既有物件版本 (Revise)
          </button>
          <button
            className={decisionKind === "dup" ? styles.primaryButton : styles.secondaryButton}
            data-testid="btn-decision-dup"
            disabled={isSelfReviewDenied || busy}
            onClick={() => setDecisionKind("dup")}
            type="button"
          >
            標記重複 (Duplicate)
          </button>
          <button
            className={decisionKind === "steward" ? styles.primaryButton : styles.secondaryButton}
            data-testid="btn-decision-steward"
            disabled={isSelfReviewDenied || busy}
            onClick={() => setDecisionKind("steward")}
            type="button"
          >
            送交治理/資料管理員 (Steward)
          </button>
        </div>

        {/* Reason Textarea */}
        <div>
          <label className={styles.fieldLabel} htmlFor="identity-decision-reason">
            決策原因 (Reason Requirement — 必填)
          </label>
          <textarea
            className={styles.textarea}
            data-testid="identity-decision-reason"
            id="identity-decision-reason"
            onChange={(e) => setReason(e.target.value)}
            placeholder="請詳細敘述圖譜變更與比對判讀理由（例如：門牌號碼一致但租金差異較大，核實為同物件跨平台更新）..."
            rows={3}
            value={reason}
          />
        </div>

        {/* Risk Summary and Checkbox */}
        <div className={styles.sectionBox} style={{ marginTop: "8px", background: "#fefce8" }}>
          <div className={styles.sectionHead} style={{ color: "#854d0e" }}>
            風險宣告與影響評估 RISK & LINEAGE IMPACT
          </div>
          <div className={styles.riskSummaryText} data-testid="identity-risk-summary">
            {riskSummaryText}
          </div>
          <label className={styles.checkboxRow} htmlFor="identity-risk-ack" style={{ marginTop: "6px" }}>
            <input
              checked={riskAcknowledged}
              data-testid="identity-risk-ack"
              disabled={isSelfReviewDenied || busy}
              id="identity-risk-ack"
              onChange={(e) => setRiskAcknowledged(e.target.checked)}
              type="checkbox"
            />
            <span>我已詳細閱讀風險評估，確認執行此圖譜變更（將連同決策原因與人員資訊寫入 Audit WORM 歷程）</span>
          </label>
        </div>

        {/* Concurrency Conflict Banner (409 OWNER_CONFLICT) */}
        {conflictState?.hasConflict ? (
          <div className={styles.errorPanel} data-testid="identity-conflict-banner" role="alert" style={{ marginTop: "10px" }}>
            <span className={styles.errorSummary}>
              ⚠ 偵測到版本與 Lock 衝突 (409 OWNER_CONFLICT)
            </span>
            <span className={styles.errorMeta}>
              最新 Owner: {conflictState.currentOwner} · 最新版本: {conflictState.currentVersion}。
              你的輸入（原因與勾選狀態）已妥善保留，請點擊下方按鈕以最新版本 If-Match 重試。
            </span>
            <div style={{ marginTop: "6px" }}>
              <button
                className={styles.primaryButton}
                data-testid="identity-conflict-refresh-btn"
                onClick={handleRefreshAndRetry}
                type="button"
              >
                重新整理資料並重新提交 (If-Match {conflictState.currentVersion})
              </button>
            </div>
          </div>
        ) : null}

        {/* Local / Server error summary */}
        {shownError ? (
          <div className={styles.errorPanel} data-testid="identity-decision-error" role="alert" style={{ marginTop: "10px" }}>
            <span className={styles.errorSummary}>{shownError}</span>
          </div>
        ) : null}

        {/* Submit action button */}
        <div style={{ marginTop: "12px", textAlign: "right" }}>
          <button
            className={styles.primaryButton}
            data-testid="identity-submit-btn"
            disabled={isSelfReviewDenied || busy || !riskAcknowledged || !reason.trim()}
            onClick={handleSubmit}
            type="button"
          >
            {busy ? "提交中…" : `確認執行${decisionTitle(decisionKind, record)} (${graphMode.toUpperCase()})`}
          </button>
        </div>
      </div>
    </div>
  );
}

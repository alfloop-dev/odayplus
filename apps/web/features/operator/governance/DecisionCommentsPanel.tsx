"use client";

import { useCallback, useEffect, useState } from "react";
import styles from "../governance.module.css";
import {
  createDecisionComment,
  editDecisionComment,
  fetchDecisionComments,
  type CommentTargetType,
  type DecisionComment,
} from "./governanceLoader";

export type DecisionCommentsPanelProps = {
  targetType: CommentTargetType;
  targetId: string;
  roleId?: string;
  canComment?: boolean;
};

function formatCommentTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString("zh-TW");
}

/**
 * Comments are rendered as an attached audit sidecar. The panel has no
 * decision controls and only sends comment body changes to the API, so it
 * cannot turn a note into an approval mutation.
 */
export function DecisionCommentsPanel({
  targetType,
  targetId,
  roleId,
  canComment = true,
}: DecisionCommentsPanelProps) {
  const [comments, setComments] = useState<DecisionComment[]>([]);
  const [draft, setDraft] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingDraft, setEditingDraft] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadComments = useCallback(async () => {
    const normalizedTargetId = targetId.trim();
    if (!normalizedTargetId) {
      setComments([]);
      return;
    }
    setLoading(true);
    setError(null);
    const rows = await fetchDecisionComments({
      targetType,
      targetId: normalizedTargetId,
      roleId,
    });
    if (rows === null) {
      setError("留言紀錄暫時無法讀取。請稍後重試。");
    } else {
      setComments(rows);
    }
    setLoading(false);
  }, [roleId, targetId, targetType]);

  useEffect(() => {
    void loadComments();
  }, [loadComments]);

  async function submitComment() {
    const content = draft.trim();
    if (!content || saving || !canComment || !targetId.trim()) return;
    setSaving(true);
    setError(null);
    const comment = await createDecisionComment({
      targetType,
      targetId: targetId.trim(),
      content,
      roleId,
    });
    if (!comment) {
      setError("留言未送出。請確認目前角色具有留言權限。");
    } else {
      setComments((current) => [...current, comment]);
      setDraft("");
    }
    setSaving(false);
  }

  async function submitEdit(commentId: string) {
    const content = editingDraft.trim();
    if (!content || saving || !canComment) return;
    setSaving(true);
    setError(null);
    const comment = await editDecisionComment({ commentId, content, roleId });
    if (!comment) {
      setError("留言未更新。只有留言作者可以編輯。");
    } else {
      setComments((current) => current.map((item) => (item.id === comment.id ? comment : item)));
      setEditingId(null);
      setEditingDraft("");
    }
    setSaving(false);
  }

  return (
    <section className={styles.commentsPanel} aria-label="Decision comments" data-testid="decision-comments-panel">
      <div className={styles.commentsHeader}>
        <div>
          <h4>討論留言</h4>
          <p>附加於 {targetType} · <code>{targetId}</code>，不會改變決策狀態。</p>
        </div>
        <span>{loading ? "讀取中…" : `${comments.length} 則`}</span>
      </div>

      {canComment ? (
        <div className={styles.commentsForm}>
          <label htmlFor="decision-comment-content">新增留言</label>
          <textarea
            aria-label="新增留言"
            id="decision-comment-content"
            onChange={(event) => setDraft(event.target.value)}
            placeholder="補充審查脈絡、待辦或證據線索…"
            rows={3}
            value={draft}
          />
          <button
            data-testid="decision-comments-create"
            disabled={saving || !draft.trim()}
            onClick={() => void submitComment()}
            type="button"
          >
            {saving ? "儲存中…" : "保留留言"}
          </button>
        </div>
      ) : (
        <div className={styles.commentsReadOnly}>目前角色僅可查看留言。</div>
      )}

      {error ? (
        <p aria-live="polite" className={styles.commentsError} role="status">
          {error}
        </p>
      ) : null}

      <div className={styles.commentsList} data-testid="decision-comments-list">
        {comments.length === 0 && !loading ? (
          <p className={styles.commentsEmpty} data-testid="decision-comments-empty">
            目前沒有留言，第一則留言會保留在這筆紀錄下。
          </p>
        ) : null}
        {comments.map((comment) => (
          <article className={styles.commentRow} key={comment.id}>
            <div className={styles.commentMeta}>
              <strong>{comment.createdBy}</strong>
              <time dateTime={comment.createdAt}>{formatCommentTime(comment.createdAt)}</time>
              {comment.edited ? <span>已編輯 {comment.editCount} 次</span> : null}
            </div>
            {editingId === comment.id ? (
              <div className={styles.commentEditForm}>
                <textarea
                  aria-label={`編輯留言 ${comment.id}`}
                  onChange={(event) => setEditingDraft(event.target.value)}
                  rows={3}
                  value={editingDraft}
                />
                <div className={styles.commentActions}>
                  <button disabled={saving || !editingDraft.trim()} onClick={() => void submitEdit(comment.id)} type="button">
                    儲存編輯
                  </button>
                  <button disabled={saving} onClick={() => setEditingId(null)} type="button">
                    取消
                  </button>
                </div>
              </div>
            ) : (
              <>
                <p className={styles.commentText}>{comment.content}</p>
                {canComment ? (
                  <button
                    className={styles.commentEditButton}
                    onClick={() => {
                      setEditingId(comment.id);
                      setEditingDraft(comment.content);
                    }}
                    type="button"
                  >
                    編輯
                  </button>
                ) : null}
              </>
            )}
          </article>
        ))}
      </div>
    </section>
  );
}

"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import type { ListingDetail } from "@oday-plus/openapi-client";
import { buildIntakeClient } from "./intakeClient";
import type { IntakeOperatorSession } from "./intakeOperatorSession";
import styles from "./intake.module.css";

type LoadState =
  | { status: "loading" }
  | { status: "ready"; detail: ListingDetail }
  | { status: "error"; code: string; summary: string };

export function ExistingListingDetailPage({
  listingId,
  operatorSession,
}: {
  listingId: string;
  operatorSession: IntakeOperatorSession;
}) {
  const client = useMemo(
    () =>
      buildIntakeClient(
        operatorSession.roleId,
        operatorSession.subjectId,
        {
          authoritative: true,
          tenantId: operatorSession.tenantId,
          systemRoles: operatorSession.systemRoles,
        },
      ),
    [operatorSession],
  );
  const [state, setState] = useState<LoadState>({ status: "loading" });

  useEffect(() => {
    let active = true;
    if (operatorSession.status !== "ready" || !client) {
      setState({
        status: "error",
        code:
          operatorSession.denialReasonCode ??
          "AUTHORIZATION_CONTEXT_UNAVAILABLE",
        summary: "目前角色無法讀取既有 Listing。",
      });
      return () => {
        active = false;
      };
    }

    setState({ status: "loading" });
    void client.getListing(listingId).then(
      (detail) => {
        if (active) setState({ status: "ready", detail });
      },
      (error: unknown) => {
        if (!active) return;
        const candidate =
          error && typeof error === "object"
            ? (error as { code?: string; detail?: string; message?: string })
            : null;
        setState({
          status: "error",
          code: candidate?.code ?? "LISTING_LOAD_FAILED",
          summary:
            candidate?.detail ??
            candidate?.message ??
            "無法讀取既有 Listing。",
        });
      },
    );
    return () => {
      active = false;
    };
  }, [client, listingId, operatorSession.denialReasonCode, operatorSession.status]);

  if (state.status === "loading") {
    return (
      <main
        aria-busy="true"
        className={styles.queue}
        data-testid="listing-detail-loading"
      >
        正在載入既有 Listing {listingId}…
      </main>
    );
  }

  if (state.status === "error") {
    return (
      <main className={styles.queue} data-testid="listing-detail-error">
        <h1>無法開啟既有物件</h1>
        <p className={styles.errorText}>
          <code>{state.code}</code>：{state.summary}
        </p>
        <Link className={styles.secondaryButton} href="/w/expansion/listings">
          返回 Listing Inbox
        </Link>
      </main>
    );
  }

  const { detail } = state;
  return (
    <main
      aria-labelledby="existing-listing-title"
      className={styles.queue}
      data-testid="listing-detail-page"
    >
      <header className={styles.queueHeader}>
        <div>
          <span className={styles.screenBadge}>Existing Listing</span>
          <h1 id="existing-listing-title">既有物件</h1>
          <code className={styles.rowId} data-testid="listing-detail-id">
            {detail.listing_id}
          </code>
        </div>
        <Link className={styles.secondaryButton} href="/w/expansion/listings">
          返回 Listing Inbox
        </Link>
      </header>

      <section className={styles.sectionBox} aria-labelledby="listing-summary-title">
        <div className={styles.sectionHead}>
          <h2 id="listing-summary-title">目前有效資料</h2>
          <span className={styles.chip}>{detail.status ?? "STATUS_UNAVAILABLE"}</span>
        </div>
        <dl className={styles.metaGrid}>
          <ListingFact label="來源" value={detail.source_id} />
          <ListingFact label="目前版本" value={detail.current_revision_id} />
          <ListingFact
            label="版本序號"
            value={detail.revision_sequence}
          />
          {Object.entries(detail.current_values).map(([key, value]) => (
            <ListingFact
              key={key}
              label={key}
              masked={detail.masked_fields?.includes(key)}
              value={value}
            />
          ))}
        </dl>
        {detail.source_url ? (
          <a href={detail.source_url} rel="noreferrer" target="_blank">
            開啟來源網站（新視窗）
          </a>
        ) : (
          <p className={styles.metaSub}>來源網址未提供或已遮罩。</p>
        )}
      </section>

      <section className={styles.sectionBox} aria-labelledby="listing-revisions-title">
        <div className={styles.sectionHead}>
          <h2 id="listing-revisions-title">ListingRevision</h2>
          <span>{detail.revisions.length} 筆</span>
        </div>
        <div className={styles.tableScroll}>
          <table className={styles.intakeTable} data-testid="listing-detail-revisions">
            <caption>既有物件的 immutable revision history</caption>
            <thead>
              <tr>
                <th scope="col">Revision ID</th>
                <th scope="col">序號</th>
                <th scope="col">狀態</th>
                <th scope="col">建立時間</th>
                <th scope="col">有效值</th>
              </tr>
            </thead>
            <tbody>
              {detail.revisions.length ? (
                detail.revisions.map((revision, index) => (
                  <tr key={String(revision.revisionId ?? index)}>
                    <td><code>{String(revision.revisionId ?? "未提供")}</code></td>
                    <td>{String(revision.sequence ?? "未提供")}</td>
                    <td>{String(revision.status ?? "未提供")}</td>
                    <td>{String(revision.createdAt ?? "未提供")}</td>
                    <td><code>{displayValue(revision.effectiveValues)}</code></td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={5}>尚無追加版本；目前顯示 Listing 原始有效資料。</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section className={styles.sectionBox} aria-labelledby="listing-identity-title">
        <div className={styles.sectionHead}>
          <h2 id="listing-identity-title">Identity lineage</h2>
          <span>{detail.identity_edges.length} 筆</span>
        </div>
        <ul className={styles.receiptList} data-testid="listing-detail-identity-edges">
          {detail.identity_edges.length ? (
            detail.identity_edges.map((edge, index) => (
              <li key={String(edge.edgeId ?? index)}>
                <code>{String(edge.edgeId ?? "未提供")}</code>
                {" · "}
                {String(edge.relation ?? edge.status ?? "未提供")}
              </li>
            ))
          ) : (
            <li>尚無 identity edge。</li>
          )}
        </ul>
      </section>
    </main>
  );
}

function ListingFact({
  label,
  masked = false,
  value,
}: {
  label: string;
  masked?: boolean;
  value: unknown;
}) {
  return (
    <div>
      <dt className={styles.metaCaption}>{label}</dt>
      <dd className={styles.metaValue}>
        {masked ? "已遮罩（FIELD_MASKED）" : displayValue(value)}
      </dd>
    </div>
  );
}

function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "未提供";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

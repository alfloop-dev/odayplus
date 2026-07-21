"use client";

import { useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { PageHeader } from "@oday-plus/ui";
import { dataStatusTone } from "@oday-plus/domain-types";
import { AssistedIntakeSection } from "../../../../../features/operator/network/intake/AssistedIntakeSection";
import styles from "../../../../../features/expansion/expansion.module.css";
import Link from "next/link";
import type { OperatorRoleId } from "../../../../../features/operator/navigation";

const pages = [
  { key: "heatzone", label: "HeatZone Radar", href: "/w/expansion/heatzone" },
  { key: "listings", label: "Listing 收件匣", href: "/w/expansion/listings" },
  { key: "candidates", label: "Candidate Sites", href: "/w/expansion/candidates" },
  { key: "sitescore", label: "SiteScore Reports", href: "/w/expansion/sitescore" },
];

export default function ListingsPage() {
  const searchParams = useSearchParams();
  const [activeRoleId, setActiveRoleId] = useState<OperatorRoleId>("expansion-manager");

  useEffect(() => {
    if (typeof window !== "undefined") {
      const urlRole = searchParams.get("role") as OperatorRoleId | null;
      const storedRole = window.sessionStorage.getItem("oday.operator.role") as OperatorRoleId | null;
      if (urlRole && ["expansion-manager", "ops-lead", "cs-lead", "field-lead", "marketing-manager", "pm-audit"].includes(urlRole)) {
        setActiveRoleId(urlRole);
      } else if (storedRole) {
        setActiveRoleId(storedRole);
      }
    }
  }, [searchParams]);

  const heatZoneId = searchParams.get("heatZone") || undefined;

  return (
    <>
      <PageHeader
        title="Listing 收件匣"
        summary="處理外部房源匯入、解析、去重、硬規則與候選點轉換。"
        breadcrumb={[
          { label: "展店 Expansion", href: "/expansion" },
          { label: "Listing 收件匣" },
        ]}
        status={{
          label: "FRESH",
          tone: dataStatusTone.FRESH,
          marker: "◆",
          "data-testid": "expansion-data-status",
        }}
        lastUpdated="2026-07-20 · model sitescore-v1.4.2"
      />
      <section aria-label="Listing inbox workspace" className="odp-content" data-testid="exp-listings-page">
        <nav className={styles.workspaceNav} aria-label="Expansion module navigation">
          <Link href="/expansion">
            Overview
          </Link>
          {pages.map((page) => (
            <Link
              aria-current={page.key === "listings" ? "page" : undefined}
              data-testid={`exp-nav-${page.key}`}
              href={page.href}
              key={page.key}
            >
              {page.label}
            </Link>
          ))}
        </nav>

        {/* Render the actual Assisted Intake experience */}
        <AssistedIntakeSection activeRoleId={activeRoleId} selectedHeatZoneId={heatZoneId} />
      </section>
    </>
  );
}

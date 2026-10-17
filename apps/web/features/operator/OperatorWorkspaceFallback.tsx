"use client";

import type { ReactNode } from "react";
import { Chip, SectionPanel, StatusBadge, TabBar } from "./components";
import { getWorkspace, type WorkspaceId } from "./navigation";
import { operatorFixturesAllowed } from "./operatorDataMode";
import styles from "./operator.module.css";

export function OperatorWorkspaceFallback({ activeRoleLabel, workspace, workspaceId }: { activeRoleLabel: string; workspace: ReturnType<typeof getWorkspace>; workspaceId: WorkspaceId }) { return <WorkspaceChrome activeRoleLabel={activeRoleLabel} workspace={workspace}><WorkspacePlaceholder workspaceId={workspaceId} /></WorkspaceChrome>; }

function WorkspaceChrome({
  activeRoleLabel,
  children,
  workspace,
}: {
  activeRoleLabel: string;
  children: ReactNode;
  workspace: ReturnType<typeof getWorkspace>;
}) {
  return (
    <>
      <div className={styles.workspaceHeader}>
        <div>
          <p>{workspace.description}</p>
          <h1>{workspace.label}</h1>
        </div>
        <div className={styles.workspaceMeta}>
          <Chip tone="info">{activeRoleLabel}</Chip>
          <Chip tone={operatorFixturesAllowed() ? "neutral" : "success"}>
            {operatorFixturesAllowed() ? "Local fixture mode" : "Live API"}
          </Chip>
          <Chip tone="neutral">Taipei / UTC+8 view</Chip>
        </div>
      </div>
      {children}
    </>
  );
}

function WorkspacePlaceholder({ workspaceId }: { workspaceId: WorkspaceId }) {
  const workspace = getWorkspace(workspaceId);

  return (
    <SectionPanel eyebrow="Fleet handoff shell" title={`${workspace.label} workspace shell`}>
      <div className={styles.placeholderLayout}>
        <div>
          <p>
            {workspace.description} 的 route、navigation guard 與 dense shell 已可渲染。後續 ODP-OC fleet 可在此掛上 typed
            fixtures、state machines 與工作流。
          </p>
          <TabBar
            items={[
              { id: "overview", label: "Overview", active: true },
              { id: "workbench", label: "Workbench", disabled: true },
              { id: "audit", label: "Audit", disabled: true },
            ]}
          />
        </div>
        <div className={styles.placeholderMatrix}>
          <StatusBadge tone="success">Shell ready</StatusBadge>
          <StatusBadge tone="info">Awaiting data worker</StatusBadge>
          <StatusBadge tone="neutral">No API import</StatusBadge>
        </div>
      </div>
    </SectionPanel>
  );
}

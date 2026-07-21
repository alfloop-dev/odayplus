/**
 * Role & workspace administration (ODP-PGAP-SHELL-001, acceptance §5).
 *
 * Every change here is a governed, audited server write — permission changes
 * are high-risk, so the surface shows its own audit trail next to the control
 * that produces it rather than hiding it in a separate module.
 *
 * A non-admin role reaching this route gets the server's 403 rendered as a
 * state, not an empty page.
 */
import { PageHeader } from "@oday-plus/ui";
import type { ShellAdminResponse } from "@oday-plus/openapi-client";
import type { ApiResource } from "./resource.ts";
import { ShellDataSource, ShellResourceState } from "./ShellStates.tsx";
import { RoleWorkspacesForm } from "./RoleWorkspacesForm.tsx";
import { formatStamp } from "./HomeWorkspace.tsx";
import styles from "./shell.module.css";

export function AdminWorkspace({ admin }: { admin: ApiResource<ShellAdminResponse> }) {
  const data = admin.data;

  return (
    <>
      <PageHeader
        title="平台管理"
        summary="角色與工作區授權管理。權限變更為高風險動作，一律寫入稽核紀錄。"
        status={
          data
            ? { label: "治理寫入", tone: "purple", marker: "•" }
            : { label: "無法取得", tone: "red", marker: "•" }
        }
        breadcrumb={[{ label: "總覽", href: "/" }, { label: "平台管理" }]}
        lastUpdated={data ? formatStamp(data.meta.generatedAt) : "—"}
      />
      <div className="odp-content" data-testid="shell-admin">
        {data ? <AdminBody data={data} admin={admin} /> : <ShellResourceState resource={admin} testId="admin-state" />}
      </div>
    </>
  );
}

function AdminBody({
  data,
  admin,
}: {
  data: ShellAdminResponse;
  admin: ApiResource<ShellAdminResponse>;
}) {
  return (
    <>
      <div className={styles.statusRow}>
        <ShellDataSource resource={admin} endpoint="/operator/shell/admin" testId="admin-data-source" />
      </div>

      <section className={styles.section} data-testid="admin-roles">
        <div className={styles.sectionHead}>
          <h2 className={styles.sectionTitle}>角色工作區授權</h2>
          <span className={styles.rowMeta}>每個角色都必須保留「今日工作」。</span>
        </div>
        <ul className={styles.list}>
          {data.roles.map((role) => (
            <li
              key={role.roleId}
              className={styles.row}
              data-testid={`admin-role-${role.roleId}`}
              data-overridden={role.overridden ? "true" : "false"}
            >
              <div className={styles.rowMain}>
                <p className={styles.rowTitle}>{role.label}</p>
                <p className={styles.rowMeta}>
                  {role.roleId} ・ {role.subtitle}
                </p>
                <p className={styles.rowMeta} data-testid={`admin-role-workspaces-${role.roleId}`}>
                  目前授權：{role.allowedWorkspaces.join("、")}
                  {role.overridden && role.updatedBy ? `（已覆寫，by ${role.updatedBy}）` : ""}
                </p>
                <RoleWorkspacesForm
                  roleId={role.roleId}
                  actingRoleId={data.meta.role?.id ?? null}
                  workspaces={data.workspaces}
                  allowed={role.allowedWorkspaces}
                />
              </div>
            </li>
          ))}
        </ul>
      </section>

      <section className={styles.section} data-testid="admin-audit">
        <div className={styles.sectionHead}>
          <h2 className={styles.sectionTitle}>近期治理紀錄</h2>
          <span className={styles.rowMeta}>由後端稽核紀錄提供，非畫面推導。</span>
        </div>
        {data.auditFeed.length === 0 ? (
          <p className="odp-muted" data-testid="admin-audit-empty">
            尚無治理變更紀錄。
          </p>
        ) : (
          <ul className={styles.list}>
            {data.auditFeed.map((event) => (
              <li key={event.id} className={styles.row} data-testid={`admin-audit-${event.id}`}>
                <div className={styles.rowMain}>
                  <p className={styles.rowTitle}>{event.message}</p>
                  <p className={styles.rowMeta}>
                    {formatStamp(event.occurredAt)} ・ {event.actorRoleId} ・ {event.action}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}

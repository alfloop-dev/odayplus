/**
 * Workspace settings (ODP-PGAP-SHELL-001, acceptance §5).
 *
 * Settings are a governed server write scoped to the acting role, not browser
 * storage — an operator's density/locale choice has to follow them to another
 * machine, and the change is audited like any other write.
 */
import { PageHeader } from "@oday-plus/ui";
import type { ShellSettingsResponse } from "@oday-plus/openapi-client";
import type { ApiResource } from "./resource.ts";
import { ShellDataSource, ShellResourceState } from "./ShellStates.tsx";
import { SettingsForm } from "./SettingsForm.tsx";
import { formatStamp } from "./HomeWorkspace.tsx";
import styles from "./shell.module.css";

export function SettingsWorkspace({ settings }: { settings: ApiResource<ShellSettingsResponse> }) {
  const data = settings.data;

  return (
    <>
      <PageHeader
        title="設定"
        summary="工作區偏好設定。設定隨角色儲存於後端，變更會寫入稽核紀錄。"
        status={
          data
            ? {
                label: data.isDefault ? "使用預設值" : "已自訂",
                tone: data.isDefault ? "gray" : "blue",
                marker: "•",
              }
            : { label: "無法取得", tone: "red", marker: "•" }
        }
        breadcrumb={[{ label: "總覽", href: "/" }, { label: "設定" }]}
        lastUpdated={data ? formatStamp(data.meta.generatedAt) : "—"}
      />
      <div className="odp-content" data-testid="shell-settings">
        {data ? (
          <>
            <div className={styles.statusRow}>
              <ShellDataSource
                resource={settings}
                endpoint="/operator/shell/settings"
                testId="settings-data-source"
              />
              {data.updatedBy ? (
                <span className={styles.rowMeta} data-testid="settings-updated-by">
                  最後由 {data.updatedBy} 於 {data.updatedAt ? formatStamp(data.updatedAt) : "—"} 更新
                </span>
              ) : null}
            </div>
            <SettingsForm
              values={data.values}
              options={data.options}
              roleId={data.meta.role?.id ?? null}
            />
          </>
        ) : (
          <ShellResourceState resource={settings} testId="settings-state" />
        )}
      </div>
    </>
  );
}

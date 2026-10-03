import React from "react";
import styles from "./operator.module.css";

export type OperatorReleaseStatus = {
  profile: "full" | "dev-admin" | null;
  models: "ready" | "limited" | "unknown";
  unavailableServices: string[];
};

/** Display-only: never grants business access or declares release acceptance. */
export function OperatorReleaseNotice({ status }: { status: OperatorReleaseStatus }) {
  return (
    <section className={styles.adminNotice} role="status" data-testid="operator-release-notice">
      <strong>發布範圍與模型狀態</strong>
      <p>
        {status.profile === "dev-admin"
          ? "dev-admin：僅限 dev 管理後台驗收，不代表完整產品、模型就緒或 production 發布驗收。"
          : status.profile === "full"
            ? "full：完整發布範圍；本頁狀態不代表完整產品或 production 發布驗收通過。"
            : "無法確認發布範圍；請檢查部署設定。"}
      </p>
      <p>
        {status.models === "limited"
          ? "正式模型綁定尚未就緒；缺少模型的功能不可用，模型相依操作仍會拒絕，不提供替代預測。"
          : status.models === "ready"
            ? "正式模型綁定已就緒；個別功能仍依實際能力與權限開放。"
            : "無法確認目前模型狀態；不可據此認定模型或功能可用，請檢查 API readiness。"}
      </p>
      {status.unavailableServices.length ? (
        <p>目前不可用的模型功能：{status.unavailableServices.join("、")}。</p>
      ) : null}
    </section>
  );
}

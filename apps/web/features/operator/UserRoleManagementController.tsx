/**
 * User & Role Management Controller UI Component (UX-SCR-ADMIN-001 / FR-OPS-003).
 *
 * Self-service user role assignment, scope dimensions (Tenant, Brand, Region, Store),
 * status toggle, and audit trail visualization for ODP-CAP-USER-ROLE-UI-001.
 */

import React, { useCallback, useEffect, useState } from "react";
import styles from "./governance.module.css";

export type UserScope = {
  tenant_id: string;
  brand_ids: string[];
  region_ids: string[];
  store_ids: string[];
  clearance: string;
};

export type UserRecord = {
  subject_id: string;
  email?: string;
  name?: string;
  roles: string[];
  scope: UserScope;
  attributes?: Record<string, any>;
  status: "active" | "disabled" | string;
  updated_at?: string;
  updated_by?: string;
  notes?: string;
};

export type UserRoleAuditRecord = {
  event_type: string;
  action: string;
  actor: string;
  resource: string;
  timestamp: string;
  detail?: Record<string, any>;
  metadata?: Record<string, any>;
  correlation_id?: string;
};

export type RoleDefinition = {
  role_id: string;
  label: string;
  description: string;
};

const CANONICAL_ROLES: RoleDefinition[] = [
  { role_id: "operations_manager", label: "營運主管", description: "全域監控、跨域指派與核准" },
  { role_id: "site_reviewer", label: "選址審核員", description: "SiteScore 評估與選址決策核決" },
  { role_id: "expansion_user", label: "展店經理", description: "HeatZone、候選點與點位提案" },
  { role_id: "regional_supervisor", label: "區域督導", description: "區域門市與處置執行" },
  { role_id: "marketing_manager", label: "行銷經理", description: "AdLift 活動與分群策略" },
  { role_id: "pricing_manager", label: "定價經理", description: "PriceOps 建議與定價變更" },
  { role_id: "platform_admin", label: "平台維運管理員", description: "系統設定與 Feature Flag 控制" },
  { role_id: "auditor", label: "PM / 稽核員", description: "模型、決策追蹤與稽核線索" },
  { role_id: "compliance_officer", label: "合規官", description: "隱私、WORM 簽章與合規處置" },
  { role_id: "executive", label: "高階主管", description: "NetPlan 策略與高風險決策核准" },
  { role_id: "franchisee", label: "加盟商", description: "加盟單店營運與閱讀導覽" },
];

const DEFAULT_USERS: UserRecord[] = [
  {
    subject_id: "ops-lead",
    email: "ops-lead@odayplus.com",
    name: "營運主管",
    roles: ["operations_manager", "regional_supervisor"],
    scope: {
      tenant_id: "tenant-default",
      brand_ids: ["brand-a", "brand-b"],
      region_ids: ["region-north", "region-south"],
      store_ids: [],
      clearance: "CONFIDENTIAL",
    },
    attributes: { department: "Operations", level: "Senior" },
    status: "active",
    updated_at: "2026-08-01T00:00:00Z",
    updated_by: "system-bootstrap",
    notes: "Default operations lead principal",
  },
  {
    subject_id: "pm-auditor",
    email: "pm-auditor@odayplus.com",
    name: "PM / 稽核",
    roles: ["auditor", "compliance_officer"],
    scope: {
      tenant_id: "tenant-default",
      brand_ids: [],
      region_ids: [],
      store_ids: [],
      clearance: "HIGHLY_RESTRICTED",
    },
    attributes: { department: "Audit", level: "Lead" },
    status: "active",
    updated_at: "2026-08-01T00:00:00Z",
    updated_by: "system-bootstrap",
    notes: "PM & Audit principal",
  },
  {
    subject_id: "marketing-lead",
    email: "marketing-lead@odayplus.com",
    name: "行銷經理",
    roles: ["marketing_manager", "pricing_manager"],
    scope: {
      tenant_id: "tenant-default",
      brand_ids: ["brand-a"],
      region_ids: [],
      store_ids: [],
      clearance: "CONFIDENTIAL",
    },
    attributes: { department: "Marketing" },
    status: "active",
    updated_at: "2026-08-01T00:00:00Z",
    updated_by: "system-bootstrap",
    notes: "Marketing lead principal",
  },
  {
    subject_id: "expansion-lead",
    email: "expansion-lead@odayplus.com",
    name: "展店經理",
    roles: ["expansion_user", "site_reviewer"],
    scope: {
      tenant_id: "tenant-default",
      brand_ids: ["brand-a", "brand-b"],
      region_ids: ["region-north"],
      store_ids: [],
      clearance: "CONFIDENTIAL",
    },
    attributes: { department: "Expansion" },
    status: "active",
    updated_at: "2026-08-01T00:00:00Z",
    updated_by: "system-bootstrap",
    notes: "Expansion & Site reviewer principal",
  },
  {
    subject_id: "platform-admin",
    email: "admin@odayplus.com",
    name: "平台維運管理員",
    roles: ["platform_admin"],
    scope: {
      tenant_id: "tenant-default",
      brand_ids: [],
      region_ids: [],
      store_ids: [],
      clearance: "HIGHLY_RESTRICTED",
    },
    attributes: { department: "Platform Ops", super_admin: true },
    status: "active",
    updated_at: "2026-08-01T00:00:00Z",
    updated_by: "system-bootstrap",
    notes: "Platform super admin",
  },
];

export type UserRoleManagementControllerProps = {
  currentRoleId?: string;
  onUserRoleChange?: (user: UserRecord) => void;
};

export function UserRoleManagementController({
  currentRoleId = "operations_manager",
  onUserRoleChange,
}: UserRoleManagementControllerProps) {
  const [users, setUsers] = useState<UserRecord[]>(DEFAULT_USERS);
  const [auditLogs, setAuditLogs] = useState<UserRoleAuditRecord[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [selectedUser, setSelectedUser] = useState<UserRecord | null>(null);
  const [isEditModalOpen, setIsEditModalOpen] = useState<boolean>(false);
  const [toastMessage, setToastMessage] = useState<string | null>(null);

  // Edit Form State
  const [editRoles, setEditRoles] = useState<string[]>([]);
  const [editTenantId, setEditTenantId] = useState<string>("tenant-default");
  const [editBrands, setEditBrands] = useState<string>("");
  const [editRegions, setEditRegions] = useState<string>("");
  const [editStores, setEditStores] = useState<string>("");
  const [editClearance, setEditClearance] = useState<string>("CONFIDENTIAL");
  const [editReason, setEditReason] = useState<string>("");
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);

  const showToast = useCallback((msg: string) => {
    setToastMessage(msg);
    setTimeout(() => setToastMessage(null), 4000);
  }, []);

  const fetchUsers = useCallback(async () => {
    setIsLoading(true);
    try {
      const res = await fetch("/api/v1/operator/users");
      if (res.ok) {
        const data = await res.json();
        if (data.users && Array.isArray(data.users)) {
          setUsers(data.users);
        }
      }
    } catch {
      // Fall back to default seeded users
    } finally {
      setIsLoading(false);
    }
  }, []);

  const fetchAuditLogs = useCallback(async () => {
    try {
      const res = await fetch("/api/v1/operator/users/audit-trail");
      if (res.ok) {
        const data = await res.json();
        if (data.events && Array.isArray(data.events)) {
          setAuditLogs(data.events);
        }
      }
    } catch {
      // Fallback
    }
  }, []);

  useEffect(() => {
    fetchUsers();
    fetchAuditLogs();
  }, [fetchUsers, fetchAuditLogs]);

  const handleOpenEdit = (user: UserRecord) => {
    setSelectedUser(user);
    setEditRoles(user.roles || []);
    setEditTenantId(user.scope?.tenant_id || "tenant-default");
    setEditBrands((user.scope?.brand_ids || []).join(", "));
    setEditRegions((user.scope?.region_ids || []).join(", "));
    setEditStores((user.scope?.store_ids || []).join(", "));
    setEditClearance(user.scope?.clearance || "CONFIDENTIAL");
    setEditReason("");
    setIsEditModalOpen(true);
  };

  const handleToggleRole = (roleId: string) => {
    setEditRoles((prev) =>
      prev.includes(roleId) ? prev.filter((r) => r !== roleId) : [...prev, roleId]
    );
  };

  const handleSaveUser = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedUser) return;
    if (editRoles.length === 0) {
      showToast("錯誤：至少需要指定一個角色權限");
      return;
    }

    setIsSubmitting(true);
    const parsedBrands = editBrands.split(",").map((s) => s.trim()).filter(Boolean);
    const parsedRegions = editRegions.split(",").map((s) => s.trim()).filter(Boolean);
    const parsedStores = editStores.split(",").map((s) => s.trim()).filter(Boolean);

    const payload = {
      subjectId: selectedUser.subject_id,
      email: selectedUser.email,
      name: selectedUser.name,
      roles: editRoles,
      scope: {
        tenant_id: editTenantId,
        brand_ids: parsedBrands,
        region_ids: parsedRegions,
        store_ids: parsedStores,
        clearance: editClearance,
      },
      status: selectedUser.status,
      reason: editReason || "角色權限異動與 Scope 調整",
      actorName: currentRoleId,
    };

    try {
      const res = await fetch("/api/v1/operator/users", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (res.ok) {
        const data = await res.json();
        const updatedUser = data.user || {
          ...selectedUser,
          roles: editRoles,
          scope: {
            tenant_id: editTenantId,
            brand_ids: parsedBrands,
            region_ids: parsedRegions,
            store_ids: parsedStores,
            clearance: editClearance,
          },
        };
        setUsers((prev) =>
          prev.map((u) => (u.subject_id === updatedUser.subject_id ? updatedUser : u))
        );
        showToast(`已成功更新 ${updatedUser.name || updatedUser.subject_id} 的角色權限，已寫入 Audit Trail`);
        if (onUserRoleChange) onUserRoleChange(updatedUser);
      } else {
        const errorData = await res.json().catch(() => ({}));
        showToast(`更新失敗：${errorData.detail || res.statusText}`);
      }
    } catch {
      // Local optimistic update fallback for offline / test
      const updatedUser: UserRecord = {
        ...selectedUser,
        roles: editRoles,
        scope: {
          tenant_id: editTenantId,
          brand_ids: parsedBrands,
          region_ids: parsedRegions,
          store_ids: parsedStores,
          clearance: editClearance,
        },
        updated_at: new Date().toISOString(),
        updated_by: currentRoleId,
        notes: editReason || "角色權限異動 (local update)",
      };
      setUsers((prev) =>
        prev.map((u) => (u.subject_id === updatedUser.subject_id ? updatedUser : u))
      );
      showToast(`已更新 ${updatedUser.name || updatedUser.subject_id} 的角色權限 (Local)`);
      if (onUserRoleChange) onUserRoleChange(updatedUser);
    } finally {
      setIsSubmitting(false);
      setIsEditModalOpen(false);
      fetchAuditLogs();
    }
  };

  const handleToggleUserStatus = async (user: UserRecord) => {
    const newStatus = user.status === "active" ? "disabled" : "active";
    try {
      const res = await fetch(`/api/v1/operator/users/${user.subject_id}/status`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          status: newStatus,
          reason: `切換帳號狀態為 ${newStatus}`,
          actorName: currentRoleId,
        }),
      });

      if (res.ok) {
        const data = await res.json();
        const targetUser = data.user || { ...user, status: newStatus };
        setUsers((prev) =>
          prev.map((u) => (u.subject_id === user.subject_id ? targetUser : u))
        );
        showToast(`已切換帳號 ${user.subject_id} 狀態為：${newStatus}`);
      }
    } catch {
      setUsers((prev) =>
        prev.map((u) =>
          u.subject_id === user.subject_id ? { ...u, status: newStatus } : u
        )
      );
      showToast(`已切換帳號 ${user.subject_id} 狀態為：${newStatus} (Local)`);
    }
  };

  const filteredUsers = users.filter((u) => {
    const q = searchQuery.toLowerCase().trim();
    if (!q) return true;
    return (
      u.subject_id.toLowerCase().includes(q) ||
      (u.name && u.name.toLowerCase().includes(q)) ||
      (u.email && u.email.toLowerCase().includes(q)) ||
      u.roles.some((r) => r.toLowerCase().includes(q))
    );
  });

  const getRoleLabel = (roleId: string): string => {
    const found = CANONICAL_ROLES.find((r) => r.role_id === roleId);
    return found ? found.label : roleId;
  };

  return (
    <div className={styles.workspace} data-testid="user-role-management-controller">
      {/* Top Banner / Summary */}
      <div className={styles.viewHeader}>
        <div>
          <h3>User & Role 自助管理與異動稽核</h3>
          <p>
            依 Tenant / Brand / Region / Store / Clearance 等 Scope 控制存取權限，所有異動即時紀錄 Audit Trail
          </p>
        </div>
        <span style={{ fontSize: "12px", color: "#5a6478" }}>
          {users.length} 位使用者 · {users.filter((u) => u.status === "active").length} 位啟用中
        </span>
      </div>

      {/* Filter Bar */}
      <div style={{ display: "flex", gap: "10px", alignItems: "center", marginBottom: "12px" }}>
        <input
          type="text"
          placeholder="搜尋使用者 ID、姓名、Email 或角色..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          style={{
            flex: 1,
            padding: "8px 12px",
            borderRadius: "6px",
            border: "1px solid #d6dce8",
            fontSize: "13px",
          }}
          data-testid="user-search-input"
        />
        <button
          type="button"
          onClick={() => {
            const newUser: UserRecord = {
              subject_id: `user-${Date.now().toString().slice(-4)}`,
              name: "新使用者",
              email: "newuser@odayplus.com",
              roles: ["operations_manager"],
              scope: {
                tenant_id: "tenant-default",
                brand_ids: [],
                region_ids: [],
                store_ids: [],
                clearance: "CONFIDENTIAL",
              },
              status: "active",
            };
            handleOpenEdit(newUser);
          }}
          style={{
            padding: "8px 14px",
            borderRadius: "6px",
            background: "#2563eb",
            color: "#ffffff",
            border: "none",
            fontSize: "13px",
            fontWeight: 600,
            cursor: "pointer",
          }}
          data-testid="add-user-button"
        >
          + 新增使用者角色
        </button>
      </div>

      {/* Users Table */}
      <div className={styles.statusCard} style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "13px" }}>
          <thead>
            <tr style={{ background: "#f8fafc", borderBottom: "1px solid #e2e8f0", textAlign: "left" }}>
              <th style={{ padding: "10px 12px" }}>使用者 ID / 姓名</th>
              <th style={{ padding: "10px 12px" }}>指派角色 (Roles)</th>
              <th style={{ padding: "10px 12px" }}>資料存取 Scope (ABAC)</th>
              <th style={{ padding: "10px 12px" }}>狀態</th>
              <th style={{ padding: "10px 12px", textAlign: "right" }}>操作</th>
            </tr>
          </thead>
          <tbody>
            {filteredUsers.map((user) => (
              <tr key={user.subject_id} style={{ borderBottom: "1px solid #edf2f7" }} data-testid={`user-row-${user.subject_id}`}>
                <td style={{ padding: "10px 12px" }}>
                  <div style={{ fontWeight: 600, color: "#1e293b" }}>{user.name || user.subject_id}</div>
                  <div style={{ fontSize: "11px", color: "#64748b" }}>{user.subject_id} · {user.email || "N/A"}</div>
                </td>
                <td style={{ padding: "10px 12px" }}>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: "4px" }}>
                    {user.roles.map((r) => (
                      <span
                        key={r}
                        style={{
                          background: "#eff6ff",
                          color: "#1d4ed8",
                          border: "1px solid #bfdbfe",
                          padding: "2px 8px",
                          borderRadius: "12px",
                          fontSize: "11px",
                          fontWeight: 600,
                        }}
                      >
                        {getRoleLabel(r)}
                      </span>
                    ))}
                  </div>
                </td>
                <td style={{ padding: "10px 12px" }}>
                  <div style={{ fontSize: "12px", color: "#475569" }}>
                    <span>租戶: <b>{user.scope?.tenant_id || "default"}</b></span>
                    {user.scope?.brand_ids?.length ? (
                      <span> · 品牌: {user.scope.brand_ids.join(", ")}</span>
                    ) : null}
                    {user.scope?.region_ids?.length ? (
                      <span> · 區域: {user.scope.region_ids.join(", ")}</span>
                    ) : null}
                    <span style={{ display: "block", fontSize: "11px", color: "#94a3b8" }}>
                      Clearance: {user.scope?.clearance || "CONFIDENTIAL"}
                    </span>
                  </div>
                </td>
                <td style={{ padding: "10px 12px" }}>
                  <span
                    style={{
                      background: user.status === "active" ? "#dcfce7" : "#fee2e2",
                      color: user.status === "active" ? "#166534" : "#991b1b",
                      padding: "3px 8px",
                      borderRadius: "10px",
                      fontSize: "11px",
                      fontWeight: 600,
                    }}
                  >
                    {user.status === "active" ? "啟用中" : "已停用"}
                  </span>
                </td>
                <td style={{ padding: "10px 12px", textAlign: "right" }}>
                  <div style={{ display: "flex", gap: "6px", justifyContent: "flex-end" }}>
                    <button
                      type="button"
                      onClick={() => handleOpenEdit(user)}
                      style={{
                        padding: "4px 10px",
                        borderRadius: "4px",
                        background: "#f1f5f9",
                        border: "1px solid #cbd5e1",
                        fontSize: "12px",
                        cursor: "pointer",
                      }}
                      data-testid={`edit-user-${user.subject_id}`}
                    >
                      編輯權限
                    </button>
                    <button
                      type="button"
                      onClick={() => handleToggleUserStatus(user)}
                      style={{
                        padding: "4px 10px",
                        borderRadius: "4px",
                        background: user.status === "active" ? "#fef2f2" : "#f0fdf4",
                        color: user.status === "active" ? "#dc2626" : "#16a34a",
                        border: `1px solid ${user.status === "active" ? "#fca5a5" : "#86efac"}`,
                        fontSize: "12px",
                        cursor: "pointer",
                      }}
                    >
                      {user.status === "active" ? "停用" : "啟用"}
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Audit Trail Section */}
      <div className={styles.statusCard} style={{ marginTop: "16px" }}>
        <div className={styles.statusCardTitle}>使用者與角色權限異動稽核紀錄 (Audit Trail)</div>
        <div style={{ display: "flex", flexDirection: "column", gap: "8px", marginTop: "8px" }}>
          {auditLogs.length === 0 ? (
            <div style={{ fontSize: "12px", color: "#64748b", fontStyle: "italic" }}>
              尚無異動紀錄，對使用者角色的調整將在此處呈現不可篡改的稽核軌跡。
            </div>
          ) : (
            auditLogs.map((log, idx) => (
              <div
                key={idx}
                style={{
                  display: "flex",
                  flexDirection: "column",
                  padding: "8px 12px",
                  background: "#f8fafc",
                  borderRadius: "6px",
                  borderLeft: "4px solid #3b82f6",
                  fontSize: "12px",
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", fontWeight: 600 }}>
                  <span>{log.action} · {log.resource}</span>
                  <span style={{ color: "#64748b", fontWeight: 400 }}>{log.timestamp}</span>
                </div>
                <div style={{ color: "#475569", marginTop: "2px" }}>
                  操作者: <b>{log.actor}</b> · Correlation ID: {log.correlation_id || "N/A"}
                </div>
                {log.detail?.reason || log.metadata?.reason ? (
                  <div style={{ color: "#0f172a", marginTop: "2px", fontStyle: "italic" }}>
                    原因: {log.detail?.reason || log.metadata?.reason}
                  </div>
                ) : null}
              </div>
            ))
          )}
        </div>
      </div>

      {/* Edit Role & Scope Modal */}
      {isEditModalOpen && selectedUser ? (
        <div
          style={{
            position: "fixed",
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            background: "rgba(15, 23, 42, 0.5)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 1000,
          }}
          data-testid="edit-role-modal"
        >
          <div
            style={{
              background: "#ffffff",
              borderRadius: "8px",
              width: "560px",
              maxWidth: "90%",
              padding: "20px",
              boxShadow: "0 20px 25px -5px rgba(0, 0, 0, 0.1)",
              maxHeight: "90vh",
              overflowY: "auto",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "14px" }}>
              <h3 style={{ margin: 0, fontSize: "16px", color: "#0f172a" }}>
                編輯使用者權限：{selectedUser.name || selectedUser.subject_id}
              </h3>
              <button
                type="button"
                onClick={() => setIsEditModalOpen(false)}
                style={{ background: "none", border: "none", fontSize: "18px", cursor: "pointer", color: "#64748b" }}
              >
                ✕
              </button>
            </div>

            <form onSubmit={handleSaveUser}>
              {/* Role Selection Checkboxes */}
              <div style={{ marginBottom: "14px" }}>
                <label style={{ display: "block", fontWeight: 600, fontSize: "13px", marginBottom: "6px" }}>
                  指派系統角色 (Canonical Roles)
                </label>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px" }}>
                  {CANONICAL_ROLES.map((role) => (
                    <label
                      key={role.role_id}
                      style={{
                        display: "flex",
                        alignItems: "flex-start",
                        gap: "6px",
                        fontSize: "12px",
                        padding: "6px 8px",
                        background: editRoles.includes(role.role_id) ? "#f0f9ff" : "#f8fafc",
                        border: `1px solid ${editRoles.includes(role.role_id) ? "#0284c7" : "#e2e8f0"}`,
                        borderRadius: "6px",
                        cursor: "pointer",
                      }}
                    >
                      <input
                        type="checkbox"
                        checked={editRoles.includes(role.role_id)}
                        onChange={() => handleToggleRole(role.role_id)}
                        style={{ marginTop: "2px" }}
                      />
                      <div>
                        <div style={{ fontWeight: 600, color: "#1e293b" }}>{role.label}</div>
                        <div style={{ fontSize: "10.5px", color: "#64748b" }}>{role.description}</div>
                      </div>
                    </label>
                  ))}
                </div>
              </div>

              {/* Scope Axes */}
              <div style={{ marginBottom: "14px" }}>
                <label style={{ display: "block", fontWeight: 600, fontSize: "13px", marginBottom: "6px" }}>
                  資料層級與存取範圍 (Scope Axes)
                </label>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "10px" }}>
                  <div>
                    <label style={{ fontSize: "11px", color: "#475569" }}>Tenant ID</label>
                    <input
                      type="text"
                      value={editTenantId}
                      onChange={(e) => setEditTenantId(e.target.value)}
                      style={{ width: "100%", padding: "6px", fontSize: "12px", borderRadius: "4px", border: "1px solid #cbd5e1" }}
                    />
                  </div>
                  <div>
                    <label style={{ fontSize: "11px", color: "#475569" }}>Clearance Level</label>
                    <select
                      value={editClearance}
                      onChange={(e) => setEditClearance(e.target.value)}
                      style={{ width: "100%", padding: "6px", fontSize: "12px", borderRadius: "4px", border: "1px solid #cbd5e1" }}
                    >
                      <option value="PUBLIC">PUBLIC</option>
                      <option value="INTERNAL">INTERNAL</option>
                      <option value="CONFIDENTIAL">CONFIDENTIAL</option>
                      <option value="RESTRICTED">RESTRICTED</option>
                      <option value="HIGHLY_RESTRICTED">HIGHLY_RESTRICTED</option>
                    </select>
                  </div>
                  <div>
                    <label style={{ fontSize: "11px", color: "#475569" }}>Brand IDs (逗號分隔)</label>
                    <input
                      type="text"
                      placeholder="e.g. brand-a, brand-b"
                      value={editBrands}
                      onChange={(e) => setEditBrands(e.target.value)}
                      style={{ width: "100%", padding: "6px", fontSize: "12px", borderRadius: "4px", border: "1px solid #cbd5e1" }}
                    />
                  </div>
                  <div>
                    <label style={{ fontSize: "11px", color: "#475569" }}>Region IDs (逗號分隔)</label>
                    <input
                      type="text"
                      placeholder="e.g. region-north, region-south"
                      value={editRegions}
                      onChange={(e) => setEditRegions(e.target.value)}
                      style={{ width: "100%", padding: "6px", fontSize: "12px", borderRadius: "4px", border: "1px solid #cbd5e1" }}
                    />
                  </div>
                </div>
              </div>

              {/* Reason for Audit Log */}
              <div style={{ marginBottom: "16px" }}>
                <label style={{ display: "block", fontWeight: 600, fontSize: "13px", marginBottom: "4px" }}>
                  變更原因 / 稽核備註 (Audit Reason)
                </label>
                <textarea
                  rows={2}
                  placeholder="請輸入權限調整原因以供 Audit Log 追蹤..."
                  value={editReason}
                  onChange={(e) => setEditReason(e.target.value)}
                  style={{ width: "100%", padding: "8px", fontSize: "12px", borderRadius: "4px", border: "1px solid #cbd5e1" }}
                  required
                />
              </div>

              {/* Modal Actions */}
              <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px" }}>
                <button
                  type="button"
                  onClick={() => setIsEditModalOpen(false)}
                  style={{
                    padding: "8px 14px",
                    borderRadius: "6px",
                    background: "#f1f5f9",
                    border: "1px solid #cbd5e1",
                    fontSize: "13px",
                    cursor: "pointer",
                  }}
                >
                  取消
                </button>
                <button
                  type="submit"
                  disabled={isSubmitting}
                  style={{
                    padding: "8px 16px",
                    borderRadius: "6px",
                    background: "#2563eb",
                    color: "#ffffff",
                    border: "none",
                    fontSize: "13px",
                    fontWeight: 600,
                    cursor: isSubmitting ? "not-allowed" : "pointer",
                  }}
                  data-testid="save-user-roles-submit"
                >
                  {isSubmitting ? "儲存中..." : "儲存異動並寫入 Audit Trail"}
                </button>
              </div>
            </form>
          </div>
        </div>
      ) : null}

      {/* Toast message */}
      {toastMessage ? (
        <div
          style={{
            position: "fixed",
            bottom: "24px",
            right: "24px",
            background: "#1e293b",
            color: "#ffffff",
            padding: "10px 16px",
            borderRadius: "6px",
            fontSize: "13px",
            boxShadow: "0 10px 15px -3px rgba(0, 0, 0, 0.3)",
            zIndex: 2000,
          }}
          data-testid="user-role-toast"
        >
          {toastMessage}
        </div>
      ) : null}
    </div>
  );
}

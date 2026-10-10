/**
 * Vitest tests for UserRoleManagementController component (ODP-CAP-USER-ROLE-UI-001).
 */

import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { UserRoleManagementController } from "../UserRoleManagementController";

/**
 * Stateful fetch stub that mirrors the real service semantics the controller
 * has to satisfy: POST upserts by `subjectId`, an omitted `attributes` key
 * preserves the stored value (see `save_user`), and the subsequent list
 * refetch is what the table renders from.
 */
function stubStatefulFetch(initialUsers: any[], invitedSubjectId?: string) {
  const serverUsers: any[] = initialUsers.map((u) => ({ ...u }));
  const posted: any[] = [];

  const mock = vi.fn().mockImplementation((url: string, options?: any) => {
    if (url.includes("/api/v1/operator/users/roles")) {
      return Promise.resolve({
        ok: true,
        json: async () => ({
          roles: [{ role_id: "operations_manager", label: "營運主管", description: "全域監控" }],
          count: 1,
        }),
      });
    }
    if (url.includes("/api/v1/operator/users/audit-trail")) {
      return Promise.resolve({ ok: true, json: async () => ({ events: [], count: 0 }) });
    }
    if (url.includes("/api/v1/operator/users") && options?.method === "POST") {
      const body = JSON.parse(options.body);
      posted.push(body);
      if (url.endsWith("/invite")) {
        return Promise.resolve({ ok: true, json: async () => ({
          status: "invited", token: "a".repeat(43),
          invitation_id: invitedSubjectId || "71b983f0-236b-441d-a586-bf7c6ea227d3",
          expires_at: "2026-10-10T16:00:00Z",
        }) });
      }
      const targetId = body.subjectId;
      const existing = serverUsers.find((u) => u.subject_id === targetId);
      const saved = {
        subject_id: targetId,
        username: body.username || existing?.username,
        email: body.email,
        name: body.name,
        roles: body.roles,
        scope: body.scope,
        attributes:
          body.attributes === undefined ? existing?.attributes ?? {} : body.attributes,
        status: body.status || "active",
      };
      if (existing) {
        Object.assign(existing, saved);
      } else {
        serverUsers.unshift(saved);
      }
      return Promise.resolve({
        ok: true,
        json: async () => ({
          user: { ...saved },
        }),
      });
    }
    if (url.includes("/api/v1/operator/users")) {
      return Promise.resolve({
        ok: true,
        json: async () => ({ users: serverUsers.map((u) => ({ ...u })) }),
      });
    }
    return Promise.resolve({ ok: true, json: async () => ({}) });
  });

  vi.stubGlobal("fetch", mock);
  return { serverUsers, posted, mock };
}

async function openInvitedCredentials() {
  const accountId = "71b983f0-236b-441d-a586-bf7c6ea227d3";
  const stub = stubStatefulFetch([], accountId);
  render(<UserRoleManagementController currentRoleId="platform-admin" />);
  fireEvent.click(await screen.findByTestId("add-user-button"));
  fireEvent.change(screen.getByTestId("edit-subject-id-input"), {
    target: { value: "invited-manager" },
  });
  fireEvent.change(screen.getByTestId("edit-email-input"), {
    target: { value: "invited@example.invalid" },
  });
  fireEvent.change(screen.getByPlaceholderText(/請輸入權限調整原因/i), {
    target: { value: "回歸測試邀請憑證交接" },
  });
  fireEvent.click(screen.getByTestId("save-user-roles-submit"));
  await screen.findByTestId("invited-credentials-modal");
  return { ...stub, accountId };
}

describe("UserRoleManagementController", () => {
  let fetchMock: any;

  beforeEach(() => {
    fetchMock = vi.fn().mockImplementation((url: string, options?: any) => {
      if (url.includes("/api/v1/operator/users/roles")) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            roles: [
              { role_id: "operations_manager", label: "營運主管", description: "全域監控" },
              { role_id: "auditor", label: "PM / 稽核員", description: "稽核" },
              { role_id: "marketing_manager", label: "行銷經理", description: "AdLift" },
            ],
            count: 3,
          }),
        });
      }
      if (url.includes("/api/v1/operator/users/audit-trail")) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ events: [], count: 0 }),
        });
      }
      if (url.endsWith("/invite") && options?.method === "POST") {
        return Promise.resolve({ ok: true, json: async () => ({
          status: "invited", token: "a".repeat(43),
          invitation_id: "71b983f0-236b-441d-a586-bf7c6ea227d3",
          expires_at: "2026-10-10T16:00:00Z",
        }) });
      }
      if (url.includes("/api/v1/operator/users")) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            users: [
              {
                subject_id: "ops-lead",
                email: "ops-lead@odayplus.com",
                name: "營運主管",
                roles: ["operations_manager", "regional_supervisor"],
                scope: {
                  tenant_id: "tenant-default",
                  brand_ids: ["brand-a"],
                  region_ids: ["region-north"],
                  store_ids: [],
                  clearance: "CONFIDENTIAL",
                },
                status: "active",
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
                status: "active",
              },
              {
                subject_id: "marketing-lead",
                email: "marketing-lead@odayplus.com",
                name: "行銷經理",
                roles: ["marketing_manager"],
                scope: {
                  tenant_id: "tenant-default",
                  brand_ids: [],
                  region_ids: [],
                  store_ids: [],
                  clearance: "CONFIDENTIAL",
                },
                status: "active",
              },
            ],
          }),
        });
      }
      return Promise.resolve({
        ok: true,
        json: async () => ({}),
      });
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("renders user table with default users and role badges", async () => {
    render(<UserRoleManagementController currentRoleId="platform-admin" />);

    expect(screen.getByTestId("user-role-management-controller")).toBeInTheDocument();
    expect(screen.getByText("User & Role 自助管理與異動稽核")).toBeInTheDocument();

    expect(await screen.findByTestId("user-row-ops-lead")).toBeInTheDocument();
    expect(screen.getByTestId("user-row-pm-auditor")).toBeInTheDocument();
    expect(screen.getByTestId("user-row-marketing-lead")).toBeInTheDocument();
  });

  it("filters user rows by search query", async () => {
    render(<UserRoleManagementController currentRoleId="platform-admin" />);

    await screen.findByTestId("user-row-ops-lead");

    const searchInput = screen.getByTestId("user-search-input");
    fireEvent.change(searchInput, { target: { value: "marketing" } });

    expect(screen.getByTestId("user-row-marketing-lead")).toBeInTheDocument();
    expect(screen.queryByTestId("user-row-ops-lead")).not.toBeInTheDocument();
  });

  it("opens edit modal and updates role assignments with audit reason", async () => {
    const handleRoleChange = vi.fn();
    render(
      <UserRoleManagementController
        currentRoleId="platform-admin"
        onUserRoleChange={handleRoleChange}
      />
    );

    const editBtn = await screen.findByTestId("edit-user-ops-lead");
    fireEvent.click(editBtn);

    expect(screen.getByTestId("edit-role-modal")).toBeInTheDocument();
    expect(screen.getByText("編輯使用者權限：營運主管")).toBeInTheDocument();

    const reasonInput = screen.getByPlaceholderText(/請輸入權限調整原因/i);
    fireEvent.change(reasonInput, { target: { value: "新增權限以應對緊急狀況" } });

    const submitBtn = screen.getByTestId("save-user-roles-submit");
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(screen.queryByTestId("edit-role-modal")).not.toBeInTheDocument();
    });
  });

  it("adds new user when clicking add user button with editable subject id", async () => {
    render(<UserRoleManagementController currentRoleId="platform-admin" />);

    await screen.findByTestId("user-row-ops-lead");

    const addBtn = screen.getByTestId("add-user-button");
    fireEvent.click(addBtn);

    expect(screen.getByTestId("edit-role-modal")).toBeInTheDocument();
    expect(screen.getByText("新增使用者角色")).toBeInTheDocument();

    const subjectIdInput = screen.getByTestId("edit-subject-id-input");
    const nameInput = screen.getByTestId("edit-name-input");
    const emailInput = screen.getByTestId("edit-email-input");

    fireEvent.change(subjectIdInput, { target: { value: "user-new-001" } });
    fireEvent.change(nameInput, { target: { value: "測試新使用者" } });
    fireEvent.change(emailInput, { target: { value: "newuser@odayplus.com" } });

    const reasonInput = screen.getByPlaceholderText(/請輸入權限調整原因/i);
    fireEvent.change(reasonInput, { target: { value: "測試新增使用者權限" } });

    const submitBtn = screen.getByTestId("save-user-roles-submit");
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(screen.queryByTestId("edit-role-modal")).not.toBeInTheDocument();
    });
  });

  it("keeps ABAC attributes when saving an existing user through the edit modal", async () => {
    const { serverUsers, posted } = stubStatefulFetch([
      {
        subject_id: "ops-lead",
        email: "ops-lead@odayplus.com",
        name: "營運主管",
        roles: ["operations_manager"],
        scope: {
          tenant_id: "tenant-default",
          brand_ids: ["brand-a"],
          region_ids: [],
          store_ids: [],
          clearance: "CONFIDENTIAL",
        },
        attributes: { department: "Ops", level: "Senior" },
        status: "active",
      },
    ]);

    render(<UserRoleManagementController currentRoleId="platform-admin" />);

    fireEvent.click(await screen.findByTestId("edit-user-ops-lead"));
    fireEvent.change(screen.getByPlaceholderText(/請輸入權限調整原因/i), {
      target: { value: "調整 Scope" },
    });
    fireEvent.click(screen.getByTestId("save-user-roles-submit"));

    await waitFor(() => {
      expect(screen.queryByTestId("edit-role-modal")).not.toBeInTheDocument();
    });

    expect(posted).toHaveLength(1);
    expect(posted[0].attributes).toEqual({ department: "Ops", level: "Senior" });
    expect(serverUsers[0].attributes).toEqual({ department: "Ops", level: "Senior" });
  });

  it("preserves unedited hidden scope axes when saving an existing user through the edit modal", async () => {
    const { serverUsers, posted } = stubStatefulFetch([
      {
        subject_id: "ops-lead",
        email: "ops-lead@odayplus.com",
        name: "營運主管",
        roles: ["operations_manager"],
        scope: {
          tenant_id: "tenant-default",
          brand_ids: ["brand-a"],
          region_ids: [],
          store_ids: [],
          assigned_area_ids: ["allowed-area"],
          heat_zone_ids: ["allowed-zone"],
          modules: ["allowed-module"],
          clearance: "CONFIDENTIAL",
        },
        status: "active",
      },
    ]);

    render(<UserRoleManagementController currentRoleId="platform-admin" />);

    fireEvent.click(await screen.findByTestId("edit-user-ops-lead"));
    fireEvent.change(screen.getByTestId("edit-name-input"), {
      target: { value: "營運主管 (已更名)" },
    });
    fireEvent.change(screen.getByPlaceholderText(/請輸入權限調整原因/i), {
      target: { value: "更名測試" },
    });
    fireEvent.click(screen.getByTestId("save-user-roles-submit"));

    await waitFor(() => {
      expect(screen.queryByTestId("edit-role-modal")).not.toBeInTheDocument();
    });

    expect(posted).toHaveLength(1);
    expect(posted[0].scope.assigned_area_ids).toEqual(["allowed-area"]);
    expect(posted[0].scope.heat_zone_ids).toEqual(["allowed-zone"]);
    expect(posted[0].scope.modules).toEqual(["allowed-module"]);
  });

  it("does not manufacture an account row before invitation acceptance", async () => {
    const { posted } = stubStatefulFetch([
      {
        subject_id: "ops-lead",
        email: "ops-lead@odayplus.com",
        name: "營運主管",
        roles: ["operations_manager"],
        scope: {
          tenant_id: "tenant-default",
          brand_ids: [],
          region_ids: [],
          store_ids: [],
          clearance: "CONFIDENTIAL",
        },
        status: "active",
      },
    ]);

    render(<UserRoleManagementController currentRoleId="platform-admin" />);

    await screen.findByTestId("user-row-ops-lead");
    fireEvent.click(screen.getByTestId("add-user-button"));

    fireEvent.change(screen.getByTestId("edit-subject-id-input"), {
      target: { value: "idp|new-operator" },
    });
    fireEvent.change(screen.getByTestId("edit-name-input"), { target: { value: "新營運人員" } });
    fireEvent.change(screen.getByTestId("edit-email-input"), {
      target: { value: "new-operator@odayplus.com" },
    });
    fireEvent.change(screen.getByPlaceholderText(/請輸入權限調整原因/i), {
      target: { value: "新增營運人員" },
    });
    fireEvent.click(screen.getByTestId("save-user-roles-submit"));

    await waitFor(() => {
      expect(screen.queryByTestId("edit-role-modal")).not.toBeInTheDocument();
    });

    expect(posted[0].username).toBe("idp|new-operator");
    expect(posted[0]).not.toHaveProperty("initialPassword");
    expect(posted[0]).not.toHaveProperty("initial_password");
    expect(screen.getByTestId("invited-credentials-modal")).toBeInTheDocument();
    expect(screen.queryByTestId("user-row-idp|new-operator")).not.toBeInTheDocument();
    expect(screen.getByTestId("user-row-ops-lead")).toBeInTheDocument();
  });

  it("displays one-time initial credentials popup upon successful invitation", async () => {
    const { posted } = stubStatefulFetch([
      {
        subject_id: "ops-lead",
        email: "ops-lead@odayplus.com",
        name: "營運主管",
        roles: ["operations_manager"],
        scope: {
          tenant_id: "tenant-default",
          brand_ids: [],
          region_ids: [],
          store_ids: [],
          clearance: "CONFIDENTIAL",
        },
        status: "active",
      },
    ]);

    render(<UserRoleManagementController currentRoleId="platform-admin" />);
    await screen.findByTestId("user-row-ops-lead");

    fireEvent.click(screen.getByTestId("add-user-button"));
    fireEvent.change(screen.getByTestId("edit-subject-id-input"), {
      target: { value: "invited-manager" },
    });
    fireEvent.change(screen.getByTestId("edit-name-input"), {
      target: { value: "邀請營運經理" },
    });
    fireEvent.change(screen.getByTestId("edit-email-input"), {
      target: { value: "invited@odayplus.com" },
    });
    expect(screen.queryByTestId("edit-initial-password-input")).not.toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText(/請輸入權限調整原因/i), {
      target: { value: "發送邀請建立新經理帳號" },
    });
    fireEvent.click(screen.getByTestId("save-user-roles-submit"));

    await waitFor(() => {
      expect(screen.getByTestId("invited-credentials-modal")).toBeInTheDocument();
    });

    expect(posted[0]).not.toHaveProperty("initialPassword");
    expect(screen.getByTestId("invited-password-display")).toHaveTextContent("a".repeat(43));
    expect(screen.getByTestId("copy-password-button")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("close-credentials-modal"));
    expect(screen.queryByTestId("invited-credentials-modal")).not.toBeInTheDocument();
  });

  it("hands off the login username, not the identity account UUID", async () => {
    const { accountId } = await openInvitedCredentials();
    const modal = screen.getByTestId("invited-credentials-modal");
    expect(modal).toHaveTextContent("invited-manager");
    expect(modal.querySelector("strong")).toHaveTextContent("invited-manager");
    expect(modal.querySelector("strong")).not.toHaveTextContent(accountId);
    expect(modal).toHaveTextContent(accountId); // separate invitation ID, not login name
    expect(modal).toHaveTextContent("系統未自動寄送邀請；收件者是否收到尚未確認");
    expect(modal).toHaveTextContent("複製成功不代表已交付或接受邀請");
    expect(screen.queryByTestId(`user-row-${accountId}`)).not.toBeInTheDocument();
  });

  it("marks copied only after the clipboard promise succeeds", async () => {
    let resolveCopy!: () => void;
    const writeText = vi.fn(() => new Promise<void>((resolve) => { resolveCopy = resolve; }));
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    await openInvitedCredentials();
    fireEvent.click(screen.getByTestId("copy-password-button"));
    expect(writeText).toHaveBeenCalledWith(expect.stringContaining(`Token: ${"a".repeat(43)}`));
    expect(writeText).toHaveBeenCalledWith(expect.stringContaining("Username: invited-manager"));
    expect(screen.getByTestId("copy-password-button")).toHaveTextContent("複製中…");
    expect(screen.getByTestId("copy-password-button")).toBeDisabled();
    expect(screen.queryByText("已複製 ✓")).not.toBeInTheDocument();
    resolveCopy();
    await waitFor(() => {
      expect(screen.getByTestId("copy-password-button")).toHaveTextContent("已複製 ✓");
    });
    expect(screen.queryByTestId("copy-password-error")).not.toBeInTheDocument();
  });

  it("keeps manual-copy access and allows retry after clipboard rejection", async () => {
    const writeText = vi.fn().mockRejectedValueOnce(new Error("Permission denied"))
      .mockResolvedValueOnce(undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    await openInvitedCredentials();
    fireEvent.click(screen.getByTestId("copy-password-button"));
    expect(await screen.findByTestId("copy-password-error")).toHaveTextContent("請手動選取");
    expect(screen.queryByText("已複製 ✓")).not.toBeInTheDocument();
    expect(screen.getByTestId("invited-password-display")).toHaveTextContent("a".repeat(43));
    fireEvent.click(screen.getByTestId("copy-password-button"));
    await waitFor(() => {
      expect(screen.getByTestId("copy-password-button")).toHaveTextContent("已複製 ✓");
    });
    expect(screen.queryByTestId("copy-password-error")).not.toBeInTheDocument();
  });

  it("reports unavailable clipboard without pretending the password was saved", async () => {
    vi.stubGlobal("navigator", {});
    await openInvitedCredentials();
    fireEvent.click(screen.getByTestId("copy-password-button"));
    expect(await screen.findByTestId("copy-password-error")).toHaveTextContent("關閉後無法再次查看");
    expect(screen.queryByText("已複製 ✓")).not.toBeInTheDocument();
    expect(screen.getByTestId("invited-password-display")).toHaveTextContent("a".repeat(43));
  });

  it("discards a late copy completion after the credential modal closes", async () => {
    let resolveCopy!: () => void;
    vi.stubGlobal("navigator", { clipboard: {
      writeText: vi.fn(() => new Promise<void>((resolve) => { resolveCopy = resolve; })),
    } });
    await openInvitedCredentials();
    fireEvent.click(screen.getByTestId("copy-password-button"));
    fireEvent.click(screen.getByTestId("close-credentials-modal"));
    resolveCopy();
    await waitFor(() => {
      expect(screen.queryByTestId("invited-credentials-modal")).not.toBeInTheDocument();
    });
    expect(screen.queryByText("已複製 ✓")).not.toBeInTheDocument();
  });

  it("emits X-Operator-Role platform-admin and X-Roles platform_admin headers", async () => {
    render(<UserRoleManagementController currentRoleId="platform-admin" />);
    await screen.findByTestId("user-row-ops-lead");

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/v1/operator/users"),
      expect.objectContaining({
        headers: expect.objectContaining({
          "X-Operator-Role": "platform-admin",
          "X-Roles": "platform_admin",
        }),
      })
    );
  });
});

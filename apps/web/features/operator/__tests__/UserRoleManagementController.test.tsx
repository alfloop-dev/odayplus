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
function stubStatefulFetch(initialUsers: any[]) {
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
      const existing = serverUsers.find((u) => u.subject_id === body.subjectId);
      const saved = {
        subject_id: body.subjectId,
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
      return Promise.resolve({ ok: true, json: async () => ({ user: { ...saved } }) });
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

  it("renders the newly created user row after a successful create", async () => {
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

    // The admin-typed subject id is what gets persisted, not a generated one.
    expect(posted[0].subjectId).toBe("idp|new-operator");

    expect(await screen.findByTestId("user-row-idp|new-operator")).toBeInTheDocument();
    expect(screen.getByTestId("user-row-ops-lead")).toBeInTheDocument();
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

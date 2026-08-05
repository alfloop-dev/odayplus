/**
 * Vitest tests for UserRoleManagementController component (ODP-CAP-USER-ROLE-UI-001).
 */

import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { UserRoleManagementController } from "../UserRoleManagementController";

describe("UserRoleManagementController", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
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
      })
    );
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("renders user table with default users and role badges", async () => {
    render(<UserRoleManagementController currentRoleId="operations_manager" />);

    expect(screen.getByTestId("user-role-management-controller")).toBeInTheDocument();
    expect(screen.getByText("User & Role 自助管理與異動稽核")).toBeInTheDocument();

    expect(await screen.findByTestId("user-row-ops-lead")).toBeInTheDocument();
    expect(screen.getByTestId("user-row-pm-auditor")).toBeInTheDocument();
    expect(screen.getByTestId("user-row-marketing-lead")).toBeInTheDocument();
  });

  it("filters user rows by search query", async () => {
    render(<UserRoleManagementController currentRoleId="operations_manager" />);

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
        currentRoleId="operations_manager"
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

  it("adds new user when clicking add user button", async () => {
    render(<UserRoleManagementController currentRoleId="operations_manager" />);

    await screen.findByTestId("user-row-ops-lead");

    const addBtn = screen.getByTestId("add-user-button");
    fireEvent.click(addBtn);

    expect(screen.getByTestId("edit-role-modal")).toBeInTheDocument();
    expect(screen.getByText(/編輯使用者權限：新使用者/i)).toBeInTheDocument();
  });
});

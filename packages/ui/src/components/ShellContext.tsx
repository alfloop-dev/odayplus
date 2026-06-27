"use client";
/**
 * Client-side shell state: active role, theme and density.
 *
 * In R0 there is no auth backend (acceptance: "shell can open without auth
 * backend"), so the active role is a local placeholder the user can switch to
 * preview role-aware navigation. Real role assignment will come from the
 * session/identity service later; the shape (`Role`) is already canonical.
 */
import { createContext, useContext, useMemo, useState } from "react";
import type { ReactNode } from "react";
import type { Role } from "@oday-plus/domain-types";
import type { ThemeName, DensityName } from "@oday-plus/design-tokens";

export type ShellState = {
  role: Role;
  setRole: (role: Role) => void;
  theme: ThemeName;
  setTheme: (theme: ThemeName) => void;
  density: DensityName;
  setDensity: (density: DensityName) => void;
};

const ShellContext = createContext<ShellState | null>(null);

export type ShellProviderProps = {
  children: ReactNode;
  initialRole?: Role;
  initialTheme?: ThemeName;
  initialDensity?: DensityName;
};

export function ShellProvider({
  children,
  initialRole = "ops_manager",
  initialTheme = "light",
  initialDensity = "comfortable",
}: ShellProviderProps) {
  const [role, setRole] = useState<Role>(initialRole);
  const [theme, setTheme] = useState<ThemeName>(initialTheme);
  const [density, setDensity] = useState<DensityName>(initialDensity);

  const value = useMemo<ShellState>(
    () => ({ role, setRole, theme, setTheme, density, setDensity }),
    [role, theme, density],
  );

  return (
    <ShellContext.Provider value={value}>
      <div
        data-theme={theme}
        data-density={density}
        style={{ display: "contents" }}
      >
        {children}
      </div>
    </ShellContext.Provider>
  );
}

export function useShell(): ShellState {
  const ctx = useContext(ShellContext);
  if (!ctx) {
    throw new Error("useShell must be used within a <ShellProvider>");
  }
  return ctx;
}

/**
 * Primitive palette and raw scales.
 *
 * Source of truth: docs/design/ODAY_PLUS_DESIGN_TOKENS.md §2, §4–§11.
 * Components MUST NOT reference primitives directly — they exist only so the
 * semantic layer (semantic.ts) can alias them. See tokens doc §1.
 */

export const primitives = {
  color: {
    neutral: {
      0: "#FFFFFF",
      50: "#F8FAFC",
      100: "#F1F5F9",
      200: "#E2E8F0",
      300: "#CBD5E1",
      400: "#94A3B8",
      500: "#64748B",
      600: "#475569",
      700: "#334155",
      800: "#1E293B",
      900: "#0F172A",
    },
    green: { 100: "#DCFCE7", 500: "#16A34A", 700: "#15803D" },
    yellow: { 100: "#FEF9C3", 500: "#CA8A04", 700: "#A16207" },
    orange: { 100: "#FFEDD5", 500: "#EA580C", 700: "#C2410C" },
    red: { 100: "#FEE2E2", 500: "#DC2626", 700: "#B91C1C" },
    blue: { 100: "#DBEAFE", 500: "#2563EB", 700: "#1D4ED8" },
    purple: { 100: "#EDE9FE", 500: "#7C3AED", 700: "#6D28D9" },
    gray: { 100: "#F1F5F9", 500: "#64748B", 700: "#334155" },
  },
} as const;

/** Typography primitives — tokens doc §4. */
export const fontFamily = {
  sans: '"Inter", "Noto Sans TC", system-ui, -apple-system, "Segoe UI", sans-serif',
  mono: '"JetBrains Mono", "Roboto Mono", "Noto Sans Mono", ui-monospace, monospace',
} as const;

export const fontSize = {
  xs: "0.75rem",
  sm: "0.875rem",
  md: "1rem",
  lg: "1.125rem",
  xl: "1.25rem",
  "2xl": "1.5rem",
  "3xl": "1.875rem",
} as const;

export const fontWeight = {
  regular: 400,
  medium: 500,
  semibold: 600,
  bold: 700,
} as const;

export const lineHeight = {
  compact: 1.25,
  normal: 1.5,
  relaxed: 1.7,
} as const;

/** Spacing — tokens doc §5 (4px base scale). */
export const space = {
  0: "0px",
  1: "4px",
  2: "8px",
  3: "12px",
  4: "16px",
  6: "24px",
  8: "32px",
  12: "48px",
  16: "64px",
} as const;

/** Radius — tokens doc §6. */
export const radius = {
  none: "0px",
  sm: "4px",
  md: "8px",
  lg: "12px",
  xl: "16px",
  full: "9999px",
} as const;

/** Elevation / shadow — tokens doc §7. */
export const elevation = {
  none: "none",
  card: "0 1px 2px rgba(15,23,42,0.06), 0 1px 3px rgba(15,23,42,0.10)",
  dropdown: "0 4px 8px rgba(15,23,42,0.10), 0 2px 4px rgba(15,23,42,0.06)",
  drawer: "-8px 0 24px rgba(15,23,42,0.12)",
  modal: "0 20px 48px rgba(15,23,42,0.24)",
  toast: "0 8px 24px rgba(15,23,42,0.18)",
} as const;

/** Z-index — tokens doc §8 (strictly increasing). */
export const zIndex = {
  base: 0,
  sticky: 100,
  dropdown: 1000,
  drawer: 1100,
  modal: 1300,
  toast: 1400,
  "command-palette": 1500,
} as const;

/** Breakpoints — tokens doc §10.1. */
export const breakpoint = {
  sm: "640px",
  md: "768px",
  lg: "1024px",
  xl: "1280px",
  "2xl": "1536px",
} as const;

/** Layout — tokens doc §10.2. */
export const layout = {
  "sidebar-width": "264px",
  "sidebar-collapsed": "64px",
  "header-height": "56px",
  "drawer-width": "420px",
  "drawer-width-wide": "560px",
  "readable-max": "768px",
  "content-gutter": space[6],
  "filter-bar-height": "52px",
} as const;

/** Motion — tokens doc §11. */
export const motion = {
  duration: {
    instant: "80ms",
    fast: "160ms",
    normal: "240ms",
    slow: "360ms",
  },
  easing: {
    standard: "cubic-bezier(0.2, 0, 0, 1)",
    emphasized: "cubic-bezier(0.2, 0, 0, 1)",
    exit: "cubic-bezier(0.4, 0, 1, 1)",
  },
} as const;

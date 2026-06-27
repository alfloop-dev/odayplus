/**
 * Theme overrides — tokens doc §12. Each theme is a partial override of the
 * `light` semantic color layer; unlisted keys inherit from light.
 * Primitives never change across themes.
 */
import { primitives } from "./primitives.ts";
import { lightColor, type SemanticColorTheme } from "./semantic.ts";

const c = primitives.color;

export type ThemeName = "light" | "dark" | "high-contrast" | "presentation";

// §12.1 dark — key differences only.
const darkOverride: SemanticColorTheme = {
  "color.bg.canvas": c.neutral[900],
  "color.bg.surface": c.neutral[800],
  "color.bg.muted": c.neutral[700],
  "color.bg.inset": c.neutral[700],
  "color.text.primary": c.neutral[50],
  "color.text.secondary": c.neutral[300],
  "color.text.muted": c.neutral[400],
  "color.border.default": c.neutral[700],
  "color.border.strong": c.neutral[600],
  "color.bg.overlay": "rgba(0,0,0,0.64)",
  // status/risk/model keep *.500 hue but use deeper soft backings (~20% alpha of *.700)
  "color.status.green-soft": "rgba(21,128,61,0.20)",
  "color.status.yellow-soft": "rgba(161,98,7,0.20)",
  "color.status.orange-soft": "rgba(194,65,12,0.20)",
  "color.status.red-soft": "rgba(185,28,28,0.20)",
  "color.status.blue-soft": "rgba(29,78,216,0.20)",
  "color.status.purple-soft": "rgba(109,40,217,0.20)",
};

// §12.2 high-contrast — pushes contrast toward WCAG AAA.
const highContrastOverride: SemanticColorTheme = {
  "color.text.primary": "#000000",
  "color.text.secondary": c.neutral[900],
  "color.border.default": c.neutral[500],
  "color.border.strong": c.neutral[700],
  // status colours move to *.700 for higher contrast; soft backings use *.100
  "color.status.green": c.green[700],
  "color.status.yellow": c.yellow[700],
  "color.status.orange": c.orange[700],
  "color.status.red": c.red[700],
  "color.status.blue": c.blue[700],
  "color.status.purple": c.purple[700],
};

// §12.3 presentation — reuses light colour, density scaling handled by `density` tokens.
const presentationOverride: SemanticColorTheme = {};

export const colorThemes: Record<ThemeName, SemanticColorTheme> = {
  light: lightColor,
  dark: { ...lightColor, ...darkOverride },
  "high-contrast": { ...lightColor, ...highContrastOverride },
  presentation: { ...lightColor, ...presentationOverride },
};

/**
 * Density tokens — tokens doc §9. Size overrides only; never change semantic
 * colour or information hierarchy.
 */
export type DensityName = "comfortable" | "compact" | "presentation";

export const density: Record<DensityName, Record<string, string>> = {
  comfortable: {
    "density.row-height": "44px",
    "density.cell-padding-y": "12px",
    "density.cell-padding-x": "16px",
    "density.control-height": "40px",
    "density.card-padding": "16px",
    "density.font-scale": "1.0",
  },
  compact: {
    "density.row-height": "36px",
    "density.cell-padding-y": "8px",
    "density.cell-padding-x": "12px",
    "density.control-height": "32px",
    "density.card-padding": "12px",
    "density.font-scale": "1.0",
  },
  presentation: {
    "density.row-height": "56px",
    "density.cell-padding-y": "16px",
    "density.cell-padding-x": "16px",
    "density.control-height": "48px",
    "density.card-padding": "24px",
    "density.font-scale": "1.125",
  },
};

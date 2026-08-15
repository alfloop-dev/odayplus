/**
 * Semantic color tokens — the ONLY color layer components may reference.
 * Source of truth: docs/design/ODAY_PLUS_DESIGN_TOKENS.md §3.
 *
 * Values below are the `light` theme. Theme overrides (dark / high-contrast)
 * live in themes.ts and replace semantic values only — primitives never change.
 */
import { primitives } from "./primitives.ts";

const c = primitives.color;

/** A flat semantic color theme keyed by dot-notation token name. */
export type SemanticColorTheme = Record<string, string>;

export const lightColor: SemanticColorTheme = {
  // §3.1 Background
  "color.bg.canvas": c.neutral[50],
  "color.bg.surface": c.neutral[0],
  "color.bg.muted": c.neutral[100],
  "color.bg.inset": c.neutral[100],
  "color.bg.success-soft": c.green[100],
  "color.bg.warning-soft": c.yellow[100],
  "color.bg.danger-soft": c.red[100],
  "color.bg.info-soft": c.blue[100],
  "color.bg.model-soft": c.purple[100],
  "color.bg.overlay": "rgba(15,23,42,0.48)",

  // §3.2 Text
  "color.text.primary": c.neutral[900],
  "color.text.secondary": c.neutral[600],
  "color.text.muted": c.neutral[400],
  "color.text.inverse": c.neutral[0],
  "color.text.link": c.blue[700],
  "color.text.success": c.green[700],
  "color.text.warning": c.yellow[700],
  "color.text.danger": c.red[700],
  "color.text.info": c.blue[700],
  "color.text.model": c.purple[700],

  // §3.3 Border
  "color.border.default": c.neutral[200],
  "color.border.strong": c.neutral[300],
  "color.border.focus": c.blue[500],
  "color.border.danger": c.red[500],
  "color.border.warning": c.yellow[500],
  "color.border.success": c.green[500],

  // §3.4 Status (bound to the §6 status language)
  "color.status.green": c.green[500],
  "color.status.yellow": c.yellow[500],
  "color.status.orange": c.orange[500],
  "color.status.red": c.red[500],
  "color.status.gray": c.gray[500],
  "color.status.blue": c.blue[500],
  "color.status.purple": c.purple[500],
  // soft backings
  "color.status.green-soft": c.green[100],
  "color.status.yellow-soft": c.yellow[100],
  "color.status.orange-soft": c.orange[100],
  "color.status.red-soft": c.red[100],
  "color.status.gray-soft": c.gray[100],
  "color.status.blue-soft": c.blue[100],
  "color.status.purple-soft": c.purple[100],
  // on-text (contrast >= 4.5:1 against the matching soft backing)
  "color.status.green-on": c.green[700],
  "color.status.yellow-on": c.yellow[700],
  "color.status.orange-on": c.orange[700],
  "color.status.red-on": c.red[700],
  "color.status.gray-on": c.gray[700],
  "color.status.blue-on": c.blue[700],
  "color.status.purple-on": c.purple[700],

  // §3.5 Model stage
  "color.model.production": c.purple[700],
  "color.model.candidate": c.purple[500],
  "color.model.shadow": c.blue[500],
  "color.model.canary": c.blue[700],
  "color.model.rollback": c.red[500],

  // §3.6 Risk scale
  "color.risk.low": c.green[500],
  "color.risk.medium": c.yellow[500],
  "color.risk.high": c.orange[500],
  "color.risk.critical": c.red[500],

  // §3.7 Map tokens
  "color.map.heat.low": "#FEF3C7",
  "color.map.heat.medium": "#FB923C",
  "color.map.heat.high": "#B91C1C",
  "color.map.risk.low": "#16A34A",
  "color.map.risk.medium": "#EA580C",
  "color.map.risk.high": "#DC2626",
  "color.map.selected": c.blue[500],
  "color.map.stale-overlay": "rgba(100,116,139,0.35)",
  "color.map.cluster": c.neutral[700],
};

/** Maps the canonical FourLight / DataStatus / ModelStatus codes to a status token key. */
export const statusTokenForFourLight = {
  GREEN: "color.status.green",
  YELLOW: "color.status.yellow",
  ORANGE: "color.status.orange",
  RED: "color.status.red",
} as const;

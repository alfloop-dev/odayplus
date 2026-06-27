/**
 * @oday-plus/design-tokens
 *
 * The single source of token VALUES for ODay Plus. Components reference the
 * semantic layer only (via CSS variables `var(--odp-...)` or the `token()`
 * helper). See docs/design/ODAY_PLUS_DESIGN_TOKENS.md.
 */
export {
  primitives,
  fontFamily,
  fontSize,
  fontWeight,
  lineHeight,
  space,
  radius,
  elevation,
  zIndex,
  breakpoint,
  layout,
  motion,
} from "./primitives.ts";

export {
  lightColor,
  statusTokenForFourLight,
  type SemanticColorTheme,
} from "./semantic.ts";

export {
  colorThemes,
  density,
  type ThemeName,
  type DensityName,
} from "./themes.ts";

export { generateCss, tokenToCssVar } from "./css.ts";

import { tokenToCssVar } from "./css.ts";

/**
 * Resolve a semantic dot-notation token to its `var(--odp-...)` reference, so
 * components stay token-only and never hard-code values (visual system §10.1).
 *
 *   token("color.status.green") -> "var(--odp-color-status-green)"
 */
export function token(dotName: string): string {
  return `var(${tokenToCssVar(dotName)})`;
}

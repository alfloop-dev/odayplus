/**
 * Badge / status chip. Tone maps to a status token; label is REQUIRED — a
 * badge must never rely on colour alone (visual system §6.2, contracts §4.2).
 */
import type { CSSProperties } from "react";
import type { StatusTone } from "@oday-plus/domain-types";

export type BadgeProps = {
  label: string;
  tone?: StatusTone;
  /** optional short glyph/pattern marker for colour-blind support */
  marker?: string;
  className?: string;
  "data-testid"?: string;
};

const toneSoftVar: Record<StatusTone, string> = {
  green: "--odp-color-status-green-soft",
  yellow: "--odp-color-status-yellow-soft",
  orange: "--odp-color-status-orange-soft",
  red: "--odp-color-status-red-soft",
  gray: "--odp-color-status-gray-soft",
  blue: "--odp-color-status-blue-soft",
  purple: "--odp-color-status-purple-soft",
};

const toneOnVar: Record<StatusTone, string> = {
  green: "--odp-color-status-green-on",
  yellow: "--odp-color-status-yellow-on",
  orange: "--odp-color-status-orange-on",
  red: "--odp-color-status-red-on",
  gray: "--odp-color-status-gray-on",
  blue: "--odp-color-status-blue-on",
  purple: "--odp-color-status-purple-on",
};

export function Badge({
  label,
  tone = "gray",
  marker,
  className,
  ...rest
}: BadgeProps) {
  const style: CSSProperties = {
    background: `var(${toneSoftVar[tone]})`,
    color: `var(${toneOnVar[tone]})`,
  };
  return (
    <span
      className={["odp-badge", className].filter(Boolean).join(" ")}
      style={style}
      data-tone={tone}
      data-testid={rest["data-testid"]}
    >
      {marker ? <span aria-hidden="true">{marker}</span> : null}
      {label}
    </span>
  );
}

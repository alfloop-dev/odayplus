import type { ReactNode } from "react";

export type TooltipProps = {
  content: ReactNode;
  trigger: ReactNode;
  delay?: number;
  className?: string;
};

export function Tooltip({ content, trigger, delay, className }: TooltipProps) {
  return (
    <span className={["odp-tooltip", className].filter(Boolean).join(" ")} data-delay={delay}>
      <span className="odp-tooltip__trigger" tabIndex={0}>
        {trigger}
      </span>
      <span className="odp-tooltip__content" role="tooltip">
        {content}
      </span>
    </span>
  );
}

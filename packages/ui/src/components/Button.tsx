import { forwardRef } from "react";
import type { ButtonHTMLAttributes, ReactNode } from "react";

export type ButtonVariant =
  | "primary"
  | "secondary"
  | "tertiary"
  | "danger"
  | "warning"
  | "success"
  | "ghost"
  | "link";

export type ButtonProps = Omit<
  ButtonHTMLAttributes<HTMLButtonElement>,
  "children" | "disabled"
> & {
  children: ReactNode;
  variant?: ButtonVariant;
  size?: "sm" | "md" | "lg";
  loading?: boolean;
  disabled?: boolean;
  disabledReason?: string;
  icon?: ReactNode;
  className?: string;
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    children,
    variant = "secondary",
    size = "md",
    loading = false,
    disabled = false,
    disabledReason,
    icon,
    className,
    type = "button",
    ...buttonProps
  },
  ref,
) {
  const unavailable = disabled || loading;
  return (
    <button
      {...buttonProps}
      ref={ref}
      type={type}
      className={["odp-button", className].filter(Boolean).join(" ")}
      data-variant={variant}
      data-size={size}
      disabled={unavailable}
      aria-disabled={unavailable || undefined}
      aria-busy={loading || undefined}
      title={disabledReason}
    >
      {loading ? <span className="odp-button__spinner" aria-hidden="true" /> : icon}
      <span>{loading ? "處理中" : children}</span>
    </button>
  );
});

import type { ButtonHTMLAttributes, ReactNode } from "react";
import styles from "./operator.module.css";

export type Tone = "neutral" | "info" | "success" | "warning" | "danger" | "accent";

function classNames(...values: Array<string | false | null | undefined>) {
  return values.filter(Boolean).join(" ");
}

export type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  size?: "sm" | "md";
};

export function Button({
  children,
  className,
  size = "md",
  type = "button",
  variant = "secondary",
  ...props
}: ButtonProps) {
  return (
    <button
      className={classNames(styles.button, styles[`button_${variant}`], styles[`button_${size}`], className)}
      type={type}
      {...props}
    >
      {children}
    </button>
  );
}

export function StatusBadge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: Tone;
}) {
  return <span className={classNames(styles.statusBadge, styles[`status_${tone}`])}>{children}</span>;
}

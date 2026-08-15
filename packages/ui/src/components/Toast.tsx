import type { ReactNode } from "react";
import type { StatusTone } from "./contracts.ts";

export type ToastProps = {
  tone?: StatusTone;
  message: string;
  action?: ReactNode;
  duration?: number;
  className?: string;
};

export function Toast({ tone = "blue", message, action, duration, className }: ToastProps) {
  return (
    <div
      className={["odp-toast", className].filter(Boolean).join(" ")}
      data-tone={tone}
      role={tone === "red" ? "alert" : "status"}
      aria-live={tone === "red" ? "assertive" : "polite"}
      data-duration={duration}
    >
      <span>{message}</span>
      {action}
    </div>
  );
}

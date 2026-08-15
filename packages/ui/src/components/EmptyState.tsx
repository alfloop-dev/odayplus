import { Button } from "./Button.tsx";
import type { ActionSpec, EmptyStateContract } from "./contracts.ts";

export type EmptyStateProps = EmptyStateContract & {
  className?: string;
  "data-testid"?: string;
};

function renderAction(action: ActionSpec) {
  const variant = action.tone === "danger" ? "danger" : action.tone === "warning" ? "warning" : "secondary";
  if (action.href && action.permitted !== false) {
    return (
      <a
        key={action.id}
        className="odp-button odp-action-link"
        data-variant={variant}
        data-size="md"
        href={action.href}
        onClick={action.onSelect}
      >
        {action.icon}
        <span>{action.label}</span>
      </a>
    );
  }

  return (
    <Button
      key={action.id}
      variant={variant}
      disabled={action.permitted === false}
      disabledReason={action.disabledReason}
      loading={action.loading}
      icon={action.icon}
      onClick={action.onSelect}
    >
      {action.label}
    </Button>
  );
}

export function EmptyState({
  title,
  description,
  nextActions,
  docLink,
  className,
  ...rest
}: EmptyStateProps) {
  return (
    <section
      className={["odp-empty", className].filter(Boolean).join(" ")}
      data-testid={rest["data-testid"] ?? "empty-state"}
    >
      <h2 className="odp-empty__title">{title}</h2>
      <p className="odp-muted">{description}</p>
      <div className="odp-actions">{nextActions.map(renderAction)}</div>
      {docLink ? (
        <a className="odp-text-link" href={docLink.href}>
          {docLink.label}
        </a>
      ) : null}
    </section>
  );
}

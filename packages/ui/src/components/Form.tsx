import type { FormEvent, ReactNode } from "react";
import { Button } from "./Button.tsx";
import type { ActionSpec, EntityRef, FieldError, FormSchema } from "./contracts.ts";

export type FormProps<TValues = Record<string, unknown>> = {
  schema?: FormSchema<TValues>;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void | Promise<void>;
  affectedEntities?: EntityRef[];
  previewBeforeSubmit?: boolean;
  fieldErrors?: FieldError[];
  submitAction?: ActionSpec;
  children: ReactNode;
  className?: string;
};

export function Form<TValues = Record<string, unknown>>({
  onSubmit,
  affectedEntities = [],
  previewBeforeSubmit = false,
  fieldErrors = [],
  submitAction,
  children,
  className,
}: FormProps<TValues>) {
  return (
    <form className={["odp-form", className].filter(Boolean).join(" ")} onSubmit={onSubmit} noValidate>
      {fieldErrors.length > 0 ? (
        <div className="odp-inline-error" role="alert">
          <strong>請修正以下欄位：</strong>
          <ul>
            {fieldErrors.map((error) => (
              <li key={`${error.field}-${error.message}`}>
                {error.field}: {error.message}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {affectedEntities.length > 0 ? (
        <section className="odp-form__affected" aria-label="Affected entities">
          <h3>即將影響的實體</h3>
          <ul>
            {affectedEntities.map((entity) => (
              <li key={`${entity.entityType}-${entity.entityId}`}>{entity.label}</li>
            ))}
          </ul>
        </section>
      ) : null}
      {previewBeforeSubmit ? (
        <p className="odp-muted">提交前將顯示預覽；高風險操作需理由與稽核紀錄。</p>
      ) : null}
      {children}
      <footer className="odp-actions">
        <Button
          type="submit"
          variant={submitAction?.tone === "danger" ? "danger" : "primary"}
          loading={submitAction?.loading}
          disabled={submitAction?.permitted === false}
          disabledReason={submitAction?.disabledReason}
        >
          {submitAction?.label ?? "提交"}
        </Button>
      </footer>
    </form>
  );
}

import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode } from "react";
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

export function Chip({
  children,
  className,
  tone = "neutral",
}: {
  children: ReactNode;
  className?: string;
  tone?: Tone;
}) {
  return <span className={classNames(styles.chip, styles[`chip_${tone}`], className)}>{children}</span>;
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

export function SectionPanel({
  actions,
  children,
  eyebrow,
  title,
}: {
  actions?: ReactNode;
  children: ReactNode;
  eyebrow?: string;
  title: string;
}) {
  return (
    <section className={styles.sectionPanel}>
      <div className={styles.sectionHeader}>
        <div>
          {eyebrow ? <p className={styles.eyebrow}>{eyebrow}</p> : null}
          <h2>{title}</h2>
        </div>
        {actions ? <div className={styles.sectionActions}>{actions}</div> : null}
      </div>
      {children}
    </section>
  );
}

export function MetricCard({
  delta,
  label,
  meta,
  tone = "neutral",
  value,
}: {
  delta?: string;
  label: string;
  meta?: string;
  tone?: Tone;
  value: string;
}) {
  return (
    <article className={classNames(styles.metricCard, styles[`metric_${tone}`])}>
      <div className={styles.metricLabel}>{label}</div>
      <div className={styles.metricValue}>{value}</div>
      <div className={styles.metricFooter}>
        {delta ? <span>{delta}</span> : null}
        {meta ? <small>{meta}</small> : null}
      </div>
    </article>
  );
}

export function QueueRow({
  description,
  id,
  meta,
  onClick,
  owner,
  status,
  time,
  title,
  tone = "neutral",
}: {
  description?: string;
  id: string;
  meta: string;
  onClick?: () => void;
  owner: string;
  status: string;
  time: string;
  title: string;
  tone?: Tone;
}) {
  return (
    <button className={styles.queueRow} onClick={onClick} type="button">
      <span className={classNames(styles.queueMarker, styles[`marker_${tone}`])} aria-hidden="true" />
      <span className={styles.queueMain}>
        <span className={styles.queueTitleLine}>
          <span className={styles.queueId}>{id}</span>
          <strong>{title}</strong>
        </span>
        {description ? <span className={styles.queueDescription}>{description}</span> : null}
        <span className={styles.queueMeta}>{meta}</span>
      </span>
      <span className={styles.queueSide}>
        <StatusBadge tone={tone}>{status}</StatusBadge>
        <span>{owner}</span>
        <time>{time}</time>
      </span>
    </button>
  );
}

export function DecisionCard({
  cta,
  id,
  meta,
  status,
  title,
  tone = "neutral",
}: {
  cta: string;
  id: string;
  meta: string;
  status: string;
  title: string;
  tone?: Tone;
}) {
  return (
    <article className={styles.decisionCard}>
      <div className={styles.decisionTopline}>
        <span>{id}</span>
        <StatusBadge tone={tone}>{status}</StatusBadge>
      </div>
      <h3>{title}</h3>
      <p>{meta}</p>
      <Button size="sm" variant={tone === "danger" ? "danger" : "primary"}>
        {cta}
      </Button>
    </article>
  );
}

export function RiskRow({
  label,
  score,
  signal,
  tone = "neutral",
}: {
  label: string;
  score: number;
  signal: string;
  tone?: Tone;
}) {
  return (
    <div className={styles.riskRow}>
      <div>
        <strong>{label}</strong>
        <span>{signal}</span>
      </div>
      <div className={styles.riskScore}>
        <span>{score}</span>
        <div className={styles.riskTrack} aria-label={`${label} risk score ${score}`}>
          <span className={classNames(styles.riskFill, styles[`risk_${tone}`])} style={{ width: `${score}%` }} />
        </div>
      </div>
    </div>
  );
}

export function AuditRow({
  actor,
  category,
  detail,
  time,
}: {
  actor: string;
  category: string;
  detail: string;
  time: string;
}) {
  return (
    <article className={styles.auditRow}>
      <span className={styles.auditDot} aria-hidden="true" />
      <div>
        <div className={styles.auditTopline}>
          <strong>{category}</strong>
          <time>{time}</time>
        </div>
        <p>{detail}</p>
        <span>{actor}</span>
      </div>
    </article>
  );
}

export function TabBar({
  items,
}: {
  items: Array<{ id: string; label: string; active?: boolean; disabled?: boolean }>;
}) {
  return (
    <div className={styles.tabBar} role="tablist">
      {items.map((item) => (
        <button
          aria-disabled={item.disabled}
          aria-selected={Boolean(item.active)}
          className={classNames(styles.tab, item.active && styles.tab_active, item.disabled && styles.tab_disabled)}
          key={item.id}
          role="tab"
          type="button"
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}

export function EvidenceCard({
  children,
  label,
  tone = "neutral",
  value,
}: {
  children?: ReactNode;
  label: string;
  tone?: Tone;
  value: string;
}) {
  return (
    <article className={classNames(styles.evidenceCard, styles[`evidence_${tone}`])}>
      <span>{label}</span>
      <strong>{value}</strong>
      {children ? <p>{children}</p> : null}
    </article>
  );
}

export function FormField({
  hint,
  label,
  ...inputProps
}: InputHTMLAttributes<HTMLInputElement> & {
  hint?: string;
  label: string;
}) {
  return (
    <label className={styles.formField}>
      <span>{label}</span>
      <input {...inputProps} />
      {hint ? <small>{hint}</small> : null}
    </label>
  );
}

export function ModalFrame({
  children,
  title,
}: {
  children: ReactNode;
  title: string;
}) {
  return (
    <div className={styles.modalFrame} role="dialog" aria-modal="true" aria-label={title}>
      <div className={styles.modalHeader}>
        <h2>{title}</h2>
      </div>
      {children}
    </div>
  );
}

export function DrawerFrame({
  children,
  title,
}: {
  children: ReactNode;
  title: string;
}) {
  return (
    <aside className={styles.drawerFrame} aria-label={title}>
      <div className={styles.drawerHeader}>
        <h2>{title}</h2>
      </div>
      {children}
    </aside>
  );
}

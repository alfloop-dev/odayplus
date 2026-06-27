/**
 * PageHeader — title, one-line summary, status badge, breadcrumb, last-updated.
 * Required on every work page (visual system §4.2). Title is the page h1.
 */
import type { ReactNode } from "react";
import { Badge } from "./Badge.tsx";
import type { BadgeProps } from "./Badge.tsx";

export type BreadcrumbItem = { label: string; href?: string };

export type PageHeaderProps = {
  title: string;
  summary?: string;
  status?: BadgeProps;
  breadcrumb?: BreadcrumbItem[];
  lastUpdated?: string;
  actions?: ReactNode;
};

export function PageHeader({
  title,
  summary,
  status,
  breadcrumb,
  lastUpdated,
  actions,
}: PageHeaderProps) {
  return (
    <div className="odp-pageheader" data-testid="page-header">
      {breadcrumb && breadcrumb.length > 0 ? (
        <nav className="odp-breadcrumb" aria-label="麵包屑">
          <ol>
            {breadcrumb.map((c, i) => (
              <li key={`${c.label}-${i}`}>
                {c.href ? <a href={c.href}>{c.label}</a> : <span>{c.label}</span>}
                {i < breadcrumb.length - 1 ? <span aria-hidden="true"> / </span> : null}
              </li>
            ))}
          </ol>
        </nav>
      ) : null}

      <div className="odp-pageheader__top">
        <div>
          <h1 className="odp-pageheader__title">{title}</h1>
          {summary ? <p className="odp-pageheader__summary">{summary}</p> : null}
        </div>
        <div className="odp-pageheader__spacer" />
        {status ? <Badge {...status} /> : null}
        {actions}
      </div>

      {lastUpdated ? (
        <p className="odp-muted" data-testid="last-updated">
          最後更新：{lastUpdated}
        </p>
      ) : null}
    </div>
  );
}

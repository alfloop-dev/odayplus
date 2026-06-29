import type { TimelineNodeSpec } from "./contracts.ts";

export type TimelineProps = {
  nodes: readonly TimelineNodeSpec[];
  title?: string;
  className?: string;
};

export function Timeline({ nodes, title = "Timeline", className }: TimelineProps) {
  return (
    <section className={["odp-timeline", className].filter(Boolean).join(" ")} aria-label={title}>
      <ol>
        {nodes.map((node) => (
          <li key={node.id} className="odp-timeline__node" tabIndex={0}>
            <div className="odp-timeline__marker" aria-hidden="true" />
            <article>
              <header className="odp-timeline__header">
                <strong>{node.eventType}</strong>
                <time dateTime={node.timestamp}>{node.timestamp}</time>
              </header>
              <p>{node.description}</p>
              <p className="odp-muted">
                {node.status} · {node.actor}
                {node.relatedArtifact ? ` · ${node.relatedArtifact.label}` : ""}
              </p>
              {node.href ? (
                <a className="odp-text-link" href={node.href}>
                  開啟節點
                </a>
              ) : null}
            </article>
          </li>
        ))}
      </ol>
    </section>
  );
}

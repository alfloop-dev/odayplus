from __future__ import annotations

import argparse
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "workloads.yaml.tpl"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
IMAGE_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]+@sha256:[0-9a-f]{64}$")
INSTANCE_RE = re.compile(r"^[a-z][a-z0-9-]{4,29}:[a-z0-9-]+:[a-z][a-z0-9-]+$")
IDENTIFIER_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def _manual_instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("manual windows must include a timezone")
    return parsed.astimezone(UTC)


def render(
    *,
    release_sha: str,
    data_image: str,
    cloud_sql_proxy_image: str,
    cloud_sql_instance: str,
    postgres_user: str,
    postgres_database: str,
    manual_start: str,
    manual_end: str,
) -> str:
    if not SHA_RE.fullmatch(release_sha):
        raise ValueError("release_sha must be a full lowercase 40-character Git SHA")
    for name, value in (
        ("data_image", data_image),
        ("cloud_sql_proxy_image", cloud_sql_proxy_image),
    ):
        if not IMAGE_RE.fullmatch(value):
            raise ValueError(f"{name} must be an immutable sha256 image reference")
    if not INSTANCE_RE.fullmatch(cloud_sql_instance):
        raise ValueError("cloud_sql_instance must be project:region:instance")
    for name, value in (
        ("postgres_user", postgres_user),
        ("postgres_database", postgres_database),
    ):
        if not IDENTIFIER_RE.fullmatch(value):
            raise ValueError(f"{name} must be a PostgreSQL identifier")
    start = _manual_instant(manual_start)
    end = _manual_instant(manual_end)
    if end - start <= timedelta(0) or end - start > timedelta(days=1):
        raise ValueError("manual window must be positive and at most one day")
    orders_history_start = end - timedelta(days=62)

    replacements = {
        "__RELEASE_SHA__": release_sha,
        "__RELEASE_SHORT__": release_sha[:12],
        "__DATA_IMAGE__": data_image,
        "__CLOUD_SQL_PROXY_IMAGE__": cloud_sql_proxy_image,
        "__CLOUD_SQL_INSTANCE__": cloud_sql_instance,
        "__POSTGRES_USER__": postgres_user,
        "__POSTGRES_DATABASE__": postgres_database,
        "__MANUAL_START__": start.isoformat().replace("+00:00", "Z"),
        "__MANUAL_END__": end.isoformat().replace("+00:00", "Z"),
        "__ORDERS_HISTORY_START__": orders_history_start.isoformat().replace(
            "+00:00", "Z"
        ),
        "__ORDERS_HISTORY_END__": end.isoformat().replace("+00:00", "Z"),
    }
    output = TEMPLATE.read_text(encoding="utf-8")
    for token, value in replacements.items():
        output = output.replace(token, value)
    unresolved = sorted(set(re.findall(r"__[A-Z0-9_]+__", output)))
    if unresolved:
        raise ValueError("Unresolved manifest tokens: " + ", ".join(unresolved))
    return output


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render immutable ODay Plus data-platform GKE workloads"
    )
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--data-image", required=True)
    parser.add_argument("--cloud-sql-proxy-image", required=True)
    parser.add_argument("--cloud-sql-instance", required=True)
    parser.add_argument("--postgres-user", default="postgres")
    parser.add_argument("--postgres-database", default="postgres")
    parser.add_argument("--manual-start", required=True)
    parser.add_argument("--manual-end", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    content = render(
        release_sha=args.release_sha,
        data_image=args.data_image,
        cloud_sql_proxy_image=args.cloud_sql_proxy_image,
        cloud_sql_instance=args.cloud_sql_instance,
        postgres_user=args.postgres_user,
        postgres_database=args.postgres_database,
        manual_start=args.manual_start,
        manual_end=args.manual_end,
    )
    if args.output:
        args.output.write_text(content, encoding="utf-8")
    else:
        print(content, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

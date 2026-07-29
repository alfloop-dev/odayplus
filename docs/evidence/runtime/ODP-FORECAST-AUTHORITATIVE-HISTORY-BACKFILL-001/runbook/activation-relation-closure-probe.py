"""Check that the set of relations the activation copies is CLOSED over what the
acceptance path actually reads, and that every one of them can be corrected.

Why this exists. Defect F was a relation that the activation copied with
``ON CONFLICT DO NOTHING`` while the ingestion updated the same row in place, so
an already-present target row could never be revisited -- 16 953 stale rows, one
of them a refund the view was still counting as revenue. The fix gave every
``core`` relation a ``refresh_key``. That answers "are the relations we listed
fixed?". It does not answer the question the defect actually raises, which is
**"is the list the right list?"** Both the fix and the audit that found the
defect took their relation set from ``ACTIVATION_RELATIONS`` itself, so a
relation the acceptance path reads and the activation never copies would be
invisible to both -- there would be nothing to compare, and no drift to report.

So this probe does not take the list from us. It derives what MUST be copied
from the target's own catalog:

  1. the transitive view-dependency closure of ``model_ready.forecast_training_
     view`` (recursively through ``pg_rewrite``/``pg_depend``, so a view built on
     a view is followed), giving the base relations the view genuinely reads;
  2. the transitive FOREIGN KEY parent closure of those base relations, because
     a copy that lands a child without its parent violates the constraint and
     the activation would not complete at all.

``required = (1) union (2)``. The probe then compares that against
``ACTIVATION_RELATIONS`` imported from the module under test -- not restated
here -- in BOTH directions, and reports, for each required relation, whether it
declares a ``refresh_key``.

The two directions mean different things and are reported separately:

  * ``missing`` -- required but not copied. This is the Defect F shape one level
    up, and it is fatal: the target would read a relation nothing maintains.
  * ``copied_beyond_requirement`` -- copied but not required. Cost, not
    correctness. Reported so the set stays honest rather than growing quietly.
  * ``required_without_refresh_key`` -- copied, but frozen on first landing.
    This is Defect F exactly. It must be empty.

What this probe deliberately does NOT claim. A relation having a ``refresh_key``
means a differing row CAN be corrected; it does not mean the target currently
agrees with the source. That is a different measurement and it has its own
receipt (``canonical_row_drift_audit.json``, re-taken after activation by the
finisher). This probe is about reachability, that one is about content, and
neither substitutes for the other -- Defect F needed both to be understood.

The registry side is reported, not required. Criterion 5 reaches the data
through ``PostgresModelReadySource``, which reads ``model_ready.view_contracts``
before it reads the view at all. That table is a TARGET-side installation
artifact -- it records which view version is installed and its installer digest
-- so it is correctly absent from the copy set: copying the source's contract
row would overwrite a statement about the target with a statement about a
different database. The probe records its state anyway, because the
criterion-5 gates that passed before activation only remain valid afterwards if
activation leaves it alone, and ``forecast_history_activation`` never writes to
``model_ready`` at all.

Read-only. Redaction: relation and constraint names, a contract state and a
version string. No store, tenant, member, order id or amount is read.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg

REPO_ROOT = Path(__file__).resolve().parents[5]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

OUT = os.environ.get(
    "PROBE_OUT",
    "/tmp/odp-forecast-evidence-stage/activation_relation_closure.json",
)

VIEW_SCHEMA = "model_ready"
VIEW_NAME = "forecast_training_view"
CONTRACT_RELATION = "model_ready.forecast_training_view"

# Recursive view-dependency closure. Starts at one view and follows every
# rewrite rule's referenced relation; when a referenced relation is itself a
# view or matview it is followed in turn, so a view built on a view does not
# hide its base tables. Self-references are dropped (a view's rule always
# depends on the view itself).
CLOSURE_SQL = """
WITH RECURSIVE seed AS (
  SELECT c.oid
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE n.nspname = %s AND c.relname = %s AND c.relkind IN ('v', 'm')
),
walk AS (
  SELECT oid FROM seed
  UNION
  SELECT refobj.oid
  FROM walk w
  JOIN pg_rewrite rw ON rw.ev_class = w.oid
  JOIN pg_depend dep ON dep.objid = rw.oid AND dep.classid = 'pg_rewrite'::regclass
  JOIN pg_class refobj ON refobj.oid = dep.refobjid
  WHERE refobj.oid <> w.oid
    AND refobj.relkind IN ('r', 'p', 'v', 'm', 'f')
)
SELECT n.nspname || '.' || c.relname AS relation, c.relkind
FROM walk w JOIN pg_class c ON c.oid = w.oid JOIN pg_namespace n ON n.oid = c.relnamespace
ORDER BY 1
"""

# Transitive FK parent closure over a starting set of relations.
FK_SQL = """
WITH RECURSIVE seed AS (
  SELECT c.oid
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE n.nspname || '.' || c.relname = ANY(%s)
),
walk AS (
  SELECT oid FROM seed
  UNION
  SELECT con.confrelid
  FROM walk w
  JOIN pg_constraint con ON con.conrelid = w.oid AND con.contype = 'f'
  WHERE con.confrelid <> w.oid
)
SELECT n.nspname || '.' || c.relname AS relation
FROM walk w JOIN pg_class c ON c.oid = w.oid JOIN pg_namespace n ON n.oid = c.relnamespace
ORDER BY 1
"""

# The individual edges, so the receipt says WHY a parent is required rather than
# only that it is.
FK_EDGE_SQL = """
SELECT (cn.nspname || '.' || cc.relname) AS child,
       (pn.nspname || '.' || pc.relname) AS parent,
       con.conname
FROM pg_constraint con
JOIN pg_class cc ON cc.oid = con.conrelid
JOIN pg_namespace cn ON cn.oid = cc.relnamespace
JOIN pg_class pc ON pc.oid = con.confrelid
JOIN pg_namespace pn ON pn.oid = pc.relnamespace
WHERE con.contype = 'f'
  AND (cn.nspname || '.' || cc.relname) = ANY(%s)
ORDER BY 1, 2
"""


def _load_activation_relations() -> list[dict[str, object]]:
    """Import the copy set from the module under test rather than restating it."""
    module = importlib.import_module("scripts.data_plane.forecast_history_activation")
    return [
        {
            "relation": rel.qualified,
            "refresh_key": list(rel.refresh_key),
            "source_predicate": rel.source_predicate,
            "prune_superseded_by": list(rel.prune_superseded_by),
        }
        for rel in module.ACTIVATION_RELATIONS
    ]


def _contract_state(cur) -> dict[str, object]:
    cur.execute("SELECT to_regclass('model_ready.view_contracts') IS NOT NULL")
    (exists,) = cur.fetchone()
    if not exists:
        return {"view_contracts_exists": False}
    cur.execute(
        "SELECT view_version, contract_state, training_enabled, blocked_reason, "
        "(installer_sha256 IS NOT NULL AND installer_sha256 <> '') "
        "FROM model_ready.view_contracts WHERE relation_name = %s",
        (CONTRACT_RELATION,),
    )
    row = cur.fetchone()
    if row is None:
        return {"view_contracts_exists": True, "contract_registered": False}
    return {
        "view_contracts_exists": True,
        "contract_registered": True,
        "view_version": row[0],
        "contract_state": row[1],
        "training_enabled": row[2],
        "blocked_reason": row[3],
        "installer_sha256_present": row[4],
    }


def main() -> int:
    target_dsn = os.environ.get("ODAY_DATABASE_URL")
    if not target_dsn:
        raise SystemExit("ODAY_DATABASE_URL is not set (source /tmp/odp-forecast-dsn.env)")

    copied = _load_activation_relations()
    copied_names = {entry["relation"] for entry in copied}

    with psycopg.connect(target_dsn) as conn, conn.cursor() as cur:
        cur.execute(CLOSURE_SQL, (VIEW_SCHEMA, VIEW_NAME))
        closure_rows = cur.fetchall()
        view_bases = sorted(
            name for name, kind in closure_rows
            if kind in ("r", "p", "f") and name != f"{VIEW_SCHEMA}.{VIEW_NAME}"
        )
        intermediate_views = sorted(
            name for name, kind in closure_rows
            if kind in ("v", "m") and name != f"{VIEW_SCHEMA}.{VIEW_NAME}"
        )

        cur.execute(FK_SQL, (view_bases,))
        fk_closure = sorted(row[0] for row in cur.fetchall())

        cur.execute(FK_EDGE_SQL, (fk_closure,))
        fk_edges = [
            {"child": r[0], "parent": r[1], "constraint": r[2]}
            for r in cur.fetchall()
            if r[1] in set(fk_closure)
        ]

        contract = _contract_state(cur)

    required = sorted(set(view_bases) | set(fk_closure))
    missing = sorted(set(required) - copied_names)
    extra = sorted(copied_names - set(required))
    by_name = {entry["relation"]: entry for entry in copied}
    without_refresh = sorted(
        name for name in required
        if name in by_name and not by_name[name]["refresh_key"]
    )

    out = {
        "artifact": "activation_relation_closure",
        "task": "ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "purpose": (
            "Defect F fixed the relations we had listed. This asks whether the LIST is "
            "right, by deriving what must be copied from the target catalog -- the view's "
            "transitive dependency closure plus the transitive FK parents of those base "
            "relations -- and comparing it against ACTIVATION_RELATIONS imported from the "
            "module under test. Both the Defect F fix and the drift audit that found it "
            "took their relation set from that same constant, so a relation the acceptance "
            "path reads and the activation never copies would have been invisible to both."
        ),
        "method": {
            "plane": "TARGET PG16 catalog only",
            "view_closure": (
                "recursive pg_rewrite/pg_depend walk from model_ready.forecast_training_view, "
                "following referenced relations that are themselves views"
            ),
            "fk_closure": "recursive pg_constraint contype='f' parent walk from those base relations",
            "copy_set": (
                "scripts.data_plane.forecast_history_activation.ACTIVATION_RELATIONS, imported "
                "rather than restated"
            ),
            "read_only": True,
            "redaction": "relation, constraint, contract state and version strings only; no row data",
        },
        "view_dependency_closure": {
            "view": f"{VIEW_SCHEMA}.{VIEW_NAME}",
            "base_relations": view_bases,
            "intermediate_views": intermediate_views,
        },
        "foreign_key_closure": {
            "relations": fk_closure,
            "edges": fk_edges,
            "why_required": (
                "the activation copies parents before children so every foreign key resolves; "
                "a required parent left uncopied does not silently degrade, it aborts the copy"
            ),
        },
        "copy_set": copied,
        "comparison": {
            "required_count": len(required),
            "copied_count": len(copied_names),
            "missing": missing,
            "missing_meaning": (
                "required by the acceptance path but never copied. Fatal, and the Defect F "
                "shape one level up: there would be no target row to compare, so no drift to "
                "report."
            ),
            "copied_beyond_requirement": extra,
            "copied_beyond_requirement_meaning": (
                "copied without being reachable from the view or its FK parents. Cost rather "
                "than correctness, reported so the set cannot grow quietly."
            ),
            "required_without_refresh_key": without_refresh,
            "required_without_refresh_key_meaning": (
                "copied but frozen on first landing -- Defect F exactly. Must be empty."
            ),
        },
        "registry_side": {
            "relation": "model_ready.view_contracts",
            "copied_by_activation": "model_ready.view_contracts" in copied_names,
            "deliberately_not_copied": (
                "it is a TARGET-side installation artifact recording which view version is "
                "installed here and its installer digest. Copying the source's row would "
                "overwrite a statement about this database with one about another. "
                "forecast_history_activation writes nothing in model_ready, which is why the "
                "criterion-5 contract gates measured before activation still hold after it."
            ),
            "state": contract,
        },
    }

    verdict_ok = not missing and not without_refresh
    out["verdict"] = {
        "closed": verdict_ok,
        "statement": (
            "Every relation the acceptance path reads is copied, and every one of them can be "
            "corrected on a re-activation."
            if verdict_ok
            else "BREACH -- see comparison.missing / comparison.required_without_refresh_key."
        ),
        "falsifiable_by": (
            "adding a join to forecast_training_view, or a foreign key to one of its base "
            "relations, without extending ACTIVATION_RELATIONS. Re-run after any change to "
            "either."
        ),
    }

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as handle:
        json.dump(out, handle, indent=2, sort_keys=False)
        handle.write("\n")
    print(json.dumps(out["comparison"], indent=2))
    print(json.dumps(out["verdict"], indent=2))
    print(f"wrote {OUT}")
    return 0 if verdict_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

# Adjust bounded continuation verification

SQLite now rechecks replacement lineage in the actual UPSERT, preventing a stale save after its earlier guard read from erasing a concurrently committed replacement. A rejected write rolls back before changing the document mirror. PostgreSQL retains its row locks; deterministic two-engine tests now force shared initial reads and competing commits before locked rereads, then check HTTP 409, SQL/document lineage and lifecycle audit.

Adjust date fields validate at the payload boundary: malformed dates return HTTP 422 without business writes. Security authorization audit remains recorded normally.

`bounded-continuation-verification.json` records source hashes, commands, parent heads, patch hashes, original exit codes and durations; adjacent logs retain all results, including the initial audit assertion failures. The six affected tests passed after correcting the audit-only assertions. Existing rollout evidence/active allowlist requirements remain unchanged; independent review and required CI are still required for merge.

# Code boundaries and removal policy

`config/code-boundaries.yaml` is the source of truth for separating the product,
product operations, the internal development platform, delivery tooling, tests,
historical evidence, and archived code. Directory placement is the primary
boundary; manifest exceptions exist only while legacy paths are migrated.

## Boundary contract

| Boundary | Product runtime | Production artifact | Removal rule |
|---|---|---|---|
| `product_system` | yes | included | remove only through product retirement |
| `product_operations_tooling` | no | excluded | remove after operational support windows end |
| `development_platform_system` | no | excluded | removable when orchestration is retired or moved |
| `development_delivery_tooling` | no | excluded | removable only when this repository is no longer maintained or released |
| `verification` | no | excluded | removable with the code/support obligation it verifies |
| `evidence_artifact` | no | excluded | removable after evidence retention obligations end |
| `archived` | no | excluded | removable only under archive retention policy |

The deployable product may import only product code. Product operations may
import product contracts. Development and delivery code may consume product
interfaces, and tests may consume any maintained scope. Dependencies in the
opposite direction are rejected.

## Artifact profiles

- `production` contains only `product_system`.
- `operations` adds separately operated deployment, migration, and data/model
  commands.
- `engineering` adds the development platform, delivery tooling, and tests.

The checked-in inventory at `docs/audits/code-boundary-inventory.csv` lists the
boundary, retention class, artifact profiles, and removal condition for every
tracked Python file.

## Enforcement

Run:

```bash
make boundary-check
```

The check fails when a Python file is unclassified, matches multiple scopes,
enters a forbidden artifact profile, violates import direction, or makes the
checked-in inventory stale. Regenerate the inventory only after reviewing the
new classification:

```bash
python3 delivery_toolchain/governance/check_code_boundaries.py --write-inventory
```

Legacy locations are migrated incrementally. A compatibility wrapper may keep
an old command/import stable, but the canonical implementation must live in its
own boundary and product code must never depend on the wrapper.

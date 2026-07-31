# ODP-PLAN-OSS-LICENSE-GATE-001 — Independent Review Round 16

- Reviewer: `CodexCoordinator`
- Reviewed exact pushed owner head: `d2792b7c47306ed0a13d678b4727b976e14bbb71`
- Result: `CHANGES_REQUESTED`
- Release / legal decision: `NO-GO`

## Confirmed correction retained

The exact owner head removes the public `allow_test_vault` constructor argument
from `AuthoritativeReceiptVerifier` and from
`validate_and_load_authority_key`. The normal production constructor remains
fail closed for repository-local keys and manifests.

## B1 — Production module still exports the complete provenance bypass

The production module `scripts.security.exemption_validator` now exports
`TestAuthoritativeReceiptVerifier`. Any application caller can import and
instantiate that class. Its overrides call
`_validate_and_load_authority_key_internal(..., is_test_seam=True)` and return
`True` unconditionally from `_check_manifest_path_provenance`. The same
production module also leaves the internal helper directly importable with the
caller-selected `is_test_seam=True` argument.

The class name and docstring do not enforce a trust boundary. This is still a
production-callable authority path that accepts a repository-local key and
caller-controlled signed manifest, so the Round-15 authority bypass remains
available.

Exact-head mutation receipt:

```json
{
  "internal_bypass_callable": true,
  "key_is_inside_repository": true,
  "manifest_error": null,
  "owner_head": "d2792b7c47306ed0a13d678b4727b976e14bbb71",
  "public_production_module_subclass_importable": true,
  "repository_local_key_loaded": true,
  "repository_local_manifest_trusted": true
}
```

The mutation imported only symbols from the production module, created a
restrictive-mode key and correctly signed manifest below `ROOT`, and
instantiated the exported subclass. Both the repository-local key and its
mandatory digests became trusted.

Required correction:

- remove every production-module symbol that can disable key or manifest
  provenance checks, including the exported test subclass and the
  caller-selectable `is_test_seam` helper path;
- move any test verifier/factory that relaxes provenance entirely under test
  support code, outside the production module;
- keep the production verifier and every production-module helper permanently
  bound to fixed external vault provenance;
- add a negative import/mutation test proving no symbol exported or directly
  callable from `scripts.security.exemption_validator` can trust a
  repository-local key or manifest;
- retain the clean-checkout fail-closed, external-vault positive, and full
  security regression tests.

## Verification receipt

```text
manual exact-head repository-local key/manifest mutation
public_production_module_subclass_importable=true
internal_bypass_callable=true
repository_local_key_loaded=true
repository_local_manifest_trusted=true
```

The task remains technical and legal `NO-GO`. Do not enable
approval-dependent exemptions, claim legal approval, open/refresh a release PR,
or deploy from this rejected head.

# ODP-P10-CAN-001-R3A Orphan Shell Retirement Addendum

- Addendum ID: `ODP-P10-CAN-001-R3A-ADD-001`
- Parent task: `ODP-P10-CAN-001-R3A`
- Status: `ready_for_pickup`
- Worktree: `/home/lupin/oday-plus-package10-final`
- Branch: `fix/package10-final-20260725`
- Reason: coordinator import audit found old OpsBoard UI code omitted from the
  original R3A inventory

## Finding

After `apps/web/src/app/OpsBoardFrame.tsx` is deleted, the following old
OpsBoard shell and navigation files have no production caller. Leaving them
exported would preserve an alternate visual implementation for a future LLM to
edit even though the routes are gone.

## Required Deletions

```text
packages/ui/src/components/AppShell.tsx
packages/ui/src/components/GlobalHeader.tsx
packages/ui/src/components/Sidebar.tsx
packages/ui/src/components/ModulePlaceholder.tsx
packages/ui/src/components/ShellContext.tsx
packages/ui/src/nav/filterNav.ts
packages/ui/src/nav/routes.ts
```

## Required Migrations

1. Remove exports for the deleted components, hooks, providers, and navigation
   helpers from `packages/ui/src/index.ts`.
2. Update the package header in `packages/ui/src/index.ts`, plus
   `packages/ui/README.md` and `packages/ui/package.json`, so they describe the
   shared ODay Plus design system and do not claim to be an OpsBoard shell.
3. In `packages/ui/src/styles/shell.css`, remove only selectors owned solely by
   the deleted shell:
   `odp-shell`, `odp-skip-link`, `odp-header*`, `odp-env-badge*`,
   `odp-iconbtn*`, `odp-sidebar*`, `odp-navlink*`, and `odp-main`.
4. Retain `.odp-select`; `packages/ui/src/components/Toolbar.tsx` still uses it.
   Retain all generic controls, page content, approval, evidence, modal, table,
   form, and accessibility styles still used by canonical runtime code.
5. Replace active OpsBoard wording in:

```text
apps/README.md
apps/web/README.md
packages/domain-types/src/nav.ts
packages/domain-types/src/roles.ts
```

Historical design/evidence documents are not deleted by this addendum. Their
authority is already superseded by the Package 10 dispatch and source archive.

## Forbidden

- Do not delete reusable generic UI components.
- Do not delete `.odp-select`.
- Do not edit Package 10 intake internals.
- Do not edit the 16 canonical E2E specs.
- Do not edit API/auth/domain behavior.
- Do not commit or push; coordinator owns persistence.

## Gates

```text
npm test --workspace=@oday-plus/web
npm run typecheck
npm run build
npx playwright test <16 committed canonical specs> --project=chromium --list
git diff --check
```

Static gates must additionally prove:

- all seven deleted paths are absent;
- `packages/ui/src/index.ts` exports none of their symbols;
- no production import references their symbols or files;
- `packages/ui/src/styles/shell.css` contains none of the retired selectors;
- `.odp-select` remains;
- active app/package source and package README/metadata contain no
  `OpsBoard` or `R0 導覽骨架` wording;
- the three canonical executable pages and 16 canonical E2E specs remain.

The R3A ACK must cite this committed addendum and record this discovery as an
other-LLM/task-inventory conflict.

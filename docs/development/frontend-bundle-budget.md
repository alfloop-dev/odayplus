# Frontend build warnings and bundle budget

Owner surface: `apps/web`
Introduced by: ODP-ENG-FRONTEND-BUILD-001

`next build` reports two things it will never fail on: CSS warnings, and the
First Load JS per route. Both are now machine enforced.

## First Load JS budget

`apps/web/bundle-budget.json` declares a gzipped First Load JS ceiling per
route, plus a `defaultFirstLoadJsKb` that any undeclared route must fit and a
`sharedFirstLoadJsKb` ceiling for the chunks every route pays for.

Check it locally after a build:

```bash
npm run build --workspace=@oday-plus/web
npm run bundle:budget --workspace=@oday-plus/web   # non-zero exit when over
npm run bundle:budget --workspace=@oday-plus/web -- --json
```

`make node-check` runs `bundle:budget` between `build` and `test`, so CI's
"Run Node workspace checks" step fails on a bundle regression.

### How the number is derived

`apps/web/scripts/bundleBudget.mjs` reproduces Next's own arithmetic from the
build output rather than scraping the printed table: for each route entry in
`.next/app-build-manifest.json` it takes the union of that entry's `.js` chunks
and every layout in its chain, gzips each chunk at level 9, and reports the
total in kB where 1 kB = 1000 bytes. Against the 2026-08-08 build this lands
within rounding of Next's table (`/operator` 273.1 vs 273 kB, shared 103.3 vs
103 kB). CSS is excluded, matching the First Load JS column.

The pure helpers are unit tested in
`apps/web/scripts/__tests__/bundleBudget.test.mjs`, so the pass/fail logic is
covered without needing a build in the test run.

### Raising a budget

Raising a number in `bundle-budget.json` is a reviewed change, not a formality.
Before you do:

1. Check whether the weight belongs on first load at all. Heavy, view-specific
   dependencies belong behind `next/dynamic` with a layout-preserving `loading`
   placeholder — that is how `/operator` went from 816 kB to 273 kB.
2. If it genuinely does belong there, attach before/after `next build` output to
   the PR showing what grew and why.

Do not delete functionality to fit a budget. Deferring a chunk is the tool;
removing a feature is not.

## CSS build warnings

`align-items: start | end` (and the other box-alignment keywords) are valid in
grid but only partially supported in flexbox, so autoprefixer warns and
`next build` finishes with "Compiled with warnings".

`apps/web/scripts/__tests__/cssFlexAlignment.test.mjs` fails the test suite if
any declaration block that sets `display: flex` also uses a bare `start` / `end`
alignment keyword. Use `flex-start` / `flex-end` there. Grid containers are left
alone — the guard only inspects flex blocks, because `align-content: start` in a
grid is correct and should stay.

## Known out-of-scope finding

`packages/ui-domain/src/styles/domain.css:239` has the same flex `align-items:
end` pattern. It produces no warning today because `@oday-plus/ui-domain` is not
a dependency of `apps/web`, so it is not in this build graph and was left
unchanged. Fix it whenever that package is wired into an app.

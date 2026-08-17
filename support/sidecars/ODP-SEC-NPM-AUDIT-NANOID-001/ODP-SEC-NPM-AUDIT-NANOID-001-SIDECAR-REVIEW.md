# Sidecar Review Packet: ODP-SEC-NPM-AUDIT-NANOID-001-SIDECAR-REVIEW

- **Task ID**: `ODP-SEC-NPM-AUDIT-NANOID-001-SIDECAR-REVIEW`
- **Parent Task**: `ODP-SEC-NPM-AUDIT-NANOID-001`
- **Parent Title**: security: bump nanoid past GHSA-2v37-7h3g-55p8
- **Parent Owner / Parent Reviewer**: `CodexCoordinator` / `Claude`
- **Parent Status**: `review_approved` (approved 2026-08-08T08:19:32Z)
- **Helper Kind**: `review_packet`
- **Sidecar Owner**: `Claude3`
- **Sidecar Reviewer**: `CodexCoordinator`
- **Phase**: Unassigned
- **Parent head reviewed by this packet**: `e497a465` (`approved_head` / `review_gate_sha`, head of PR #693 *and* PR #692)
- **Last Updated**: 2026-08-17 (round 2b — base re-composed to `origin/dev`
  `078ed156`; CI-failure triage recorded in §6.9)
- **Round 1 status**: 2026-08-08T08:57Z. §1–§5 below are preserved verbatim as
  the point-in-time record. **B1 and B2 are now resolved** — PR #693 merged at
  2026-08-08T09:34:01Z and PR #692 was closed. Read §6 before acting on §3's
  recovery path; it is already carried out.

---

## Executive Summary

This is a review packet for parent task `ODP-SEC-NPM-AUDIT-NANOID-001`, which is
already `review_approved`. The packet therefore does two things: it independently
reproduces the parent's security claim, and it reports what stands between that
approval and a successful `done`.

**The fix itself is correct and independently reproduced.** The gate command
exits `1` at the parent's base and `0` at the approved head, with the nanoid
advisory named in the failure output (§4.2). This is the strongest form of
evidence available for a lockfile change and it holds.

**The blocking finding is delivery provenance, not code.** The approved content
is *already on `dev`* — but as a **different commit**, `0e14ff41`, which reached
`dev` through **PR #668** (`task/ODP-CAP-FEATURE-FLAG-UI-001`, merged
08:42:34Z), not through this task's own PR. The parent's `approved_head`
`e497a465` is **not an ancestor of `origin/dev`**. `ai_status.py`'s
`require_merged_pr` gate demands a *merged PR whose head SHA equals
`approved_head`* (`scripts/ai_status.py:1955-2000`), so **`done` will hard-fail
today** even though the vulnerability is objectively fixed in `dev`. See **B1**.

**The recovery is to let PR #693 merge, not to close it as redundant.** Its
remaining content diff against `dev` is empty (§4.4) — merging it is a no-op to
the tree but is the only thing that makes `e497a465` an ancestor of `dev` and
unblocks closeout. Closing #693 as "already delivered" would strand the parent
in `review_approved` permanently.

Three secondary findings: a duplicate open PR on a non-task branch (**B2**), no
task trailers on either commit (**B3**), and a precision correction to the
commit message's "0 vulnerabilities" claim (**O1**).

Sidecar scope: support artifacts only. No canonical L1 truth, contract, runtime,
registry, or governance file is modified by this task.

### Parent diff under review

```
$ git diff --stat origin/dev...e497a465
 .../completion/ODP-PGAP-SUPPLY-001/sbom.json       | 22 +++++++++++-----------
 package-lock.json                                  | 14 +++++++-------
 2 files changed, 18 insertions(+), 18 deletions(-)

$ git log --oneline origin/dev..origin/task/ODP-SEC-NPM-AUDIT-NANOID-001
e497a465 security: bump nanoid past GHSA-2v37-7h3g-55p8
```

One substantive commit, two files, no `dev` merges on the branch.

### PR state at the time of writing (2026-08-08T08:57Z)

| Field | PR #693 (task branch) | PR #692 (duplicate) |
|---|---|---|
| head branch | `task/ODP-SEC-NPM-AUDIT-NANOID-001` | `fix/npm-audit-nanoid` |
| head SHA | `e497a465` | `e497a465` — **same commit** |
| base | `dev` | `dev` |
| created | 08:43:23Z | 07:09:12Z |
| `state` / `mergeable` | OPEN / `MERGEABLE` | OPEN |
| `mergeStateStatus` | `BLOCKED` | `BLOCKED` |
| auto-merge | armed 08:43:43Z by `ajoe734` (method `MERGE`) | **none** |
| `orchestrator` | pass | pass |
| `performance-gate` | pass | pass |
| `product-e2e-gate` | pass | pass |
| `task-review-gate` | pass — *"Approved by assigned reviewer Claude"* | pass (shared SHA) |
| `product` | **in_progress** since 08:43:29Z | **in_progress** (same run) |

Both PRs point at the same SHA, so they share one set of check-runs — which is
why the non-task PR #692 displays a green `task-review-gate` it did not earn.
Note also that `product` **already completed `success` on this exact commit** at
07:09:18Z (§4.6); the current `in_progress` is a re-run triggered by PR #693's
creation, not a first attempt.

---

## 1. Defect Analysis & Root Cause

### The primitive

`npm audit` resolves the installed dependency graph against the **live** GitHub
advisory database at the moment it runs. The repository's supply-chain gate is:

```python
# tests/security/test_supply_chain_security_gate.py:33-37
def test_npm_audit_passes() -> None:
    res = subprocess.run(
        ["npm", "audit", "--omit=dev", "--audit-level=high"], cwd=ROOT, ...
    )
    assert res.returncode == 0, ...
```

Nothing in that command is pinned to a point in time. The lockfile is frozen;
the oracle it is judged against is not. So a gate that was green yesterday can
fail today with **zero repository change** — which is exactly what happened:
advisory `GHSA-2v37-7h3g-55p8` (`nanoid <3.3.17`, custom generators loop
indefinitely when `size` is zero) published after the last green run.

This is worth stating precisely because it inverts the usual reading of a red
gate. The failure was not caused by the change under test; it was caused by the
world moving underneath an unchanged lockfile. The parent commit message
diagnoses this correctly.

### Why `--omit=dev` still reaches nanoid

A reasonable reviewer objection: the root `package.json` declares **no
production `dependencies` at all** — only four `devDependencies` (`@axe-core/playwright`,
`@playwright/test`, `@vitejs/plugin-react`, `vitest`). If `--omit=dev` pruned to
the root's production set, the gate would inspect nearly nothing and the whole
exercise would be theatre.

It does not, because the root is a **workspace root** (`"workspaces": ["apps/web",
"packages/*"]`). Workspace packages' production dependencies stay in scope, and
`nanoid` and `postcss` arrive through `apps/web` → `next`. The pre-fix run in
§4.2 confirms this empirically: the gate command *does* fail on nanoid at the
base commit. The gate is real.

### The vulnerability's actual reachability

Honest framing for the parent owner: `GHSA-2v37-7h3g-55p8` is an infinite-loop
DoS reachable only when a caller passes `size = 0` to a **custom** nanoid
generator. In this repository nanoid is a transitive dependency of `next`, not
called directly by product code. Practical exploitability here is low.

That does not weaken the case for the bump — a high-severity advisory in the
production graph fails the gate regardless of reachability, and the fix is a
7-line patch bump with no API surface. It does mean the parent should be read as
**"restore the gate to green with a non-breaking patch bump"**, which is a
maintenance action, not **"close an exploitable hole"**. The commit message
already frames it this way.

---

## 2. Parent Implementation Assessment

### The change

`npm audit fix --package-lock-only`, producing exactly two version moves plus a
regenerated SBOM:

| Package | Before | After | Advisory | Severity vs gate |
|---|---|---|---|---|
| `nanoid` | 3.3.16 | **3.3.18** | `GHSA-2v37-7h3g-55p8` | **high — above threshold, this is the gate break** |
| `postcss` | 8.5.22 | **8.5.26** | `GHSA-fxqj-rqcc-2cmp` | moderate — below threshold, came along free |

Assessment of the choices:

- **`--package-lock-only` is the right instrument.** It edits `package-lock.json`
  without touching `node_modules` or `package.json`, so the diff is exactly the
  resolution change and nothing else. 14 lines in the lockfile, 7 each way.
- **Both moves are patch-level within the same minor.** `3.3.16 → 3.3.18` and
  `8.5.22 → 8.5.26`. No semver range in `package.json` changes; no consumer can
  observe an API difference.
- **The postcss bump is genuinely free, and this is the non-obvious part.** At
  the base commit `npm audit` offers postcss only via `npm audit fix --force`,
  warning *"Will install next@16.3.0, which is a breaking change"* (§4.2). The
  parent did **not** take that path. The existing `"overrides": {"postcss":
  "^8.5.10"}` in `package.json` lets the lockfile resolve postcss to 8.5.26
  independently of what `next` requests, so the moderate finding clears without
  the breaking `next` upgrade. Taking the `--force` route would have been a
  materially riskier change for a below-threshold finding; not taking it was the
  correct call.
- **The SBOM regeneration is mandatory, not cosmetic.** `test_sbom_and_provenance_present_and_valid`
  re-runs `scripts/security/generate_sbom.py` and asserts the committed
  `components` list equals the freshly generated one. Any lockfile version move
  makes the committed SBOM stale and fails that test. So the 22-line SBOM diff
  is a *required consequence* of the 14-line lockfile diff, not scope creep.
  Verified green at the approved head (§4.3), and verified to be a real
  fail-closed check via the negative test `test_invalid_provenance_rejected_negative`.

### Review observations on the parent diff

1. **`--audit-level=high` means the gate is silent on moderate findings.**
   The postcss moderate was fixed here only because it rode along with an
   unrelated `audit fix`. Had nanoid not broken, `GHSA-fxqj-rqcc-2cmp`
   (attacker-controlled `sourceMappingURL` reading arbitrary `.map` files) would
   have sat in the production graph indefinitely, invisible to CI. Non-blocking
   for this task; worth a conscious decision about whether `moderate` should warn
   somewhere even if it does not gate.

2. **No test pins the specific advisory.** `test_postcss_advisory_resolved`
   asserts a concrete floor on the postcss *version* — but there is no equivalent
   `test_nanoid_advisory_resolved`. The nanoid fix is protected only by the live
   `npm audit` call, which is by construction non-deterministic. If a future
   dependency resolution walked nanoid back below 3.3.17, the only thing that
   would catch it is a network-dependent audit call. Adding a four-line version
   assertion mirroring the postcss one would make this diff self-pinning. This is
   the one test-side gap worth acting on and it is cheap. Non-blocking.

3. **The `Verified:` claim is a commit-body sentence, not a trailer.** The body
   ends with `tests/security/test_supply_chain_security_gate.py: 13 passed.`
   rather than a `Verified:` trailer. The closeout spec asks for
   `Verified: <command summary>` when checks ran. Cosmetic, but see **B3** — the
   commit is missing the *required* trailers too.

4. **The advisory clock will strike again.** The mechanism that broke this gate
   is not fixed by this task and cannot be. Today, with the fix in place, the
   full tree (not `--omit=dev`) already carries two more high advisories —
   `brace-expansion` (`GHSA-mh99-v99m-4gvg`, `GHSA-rgw5-rvv9-x895`) and `js-yaml`
   (`GHSA-5p4m-2wfm-xmqj`) — both dev-only today and therefore below the gate
   (§4.5). If either package ever enters the production graph, or the gate ever
   drops `--omit=dev`, an unrelated PR gets an unexplained red check. A scheduled
   audit job would surface this as a dated alert instead of as a random PR
   failure blocking an unrelated lane — which is precisely the failure mode that
   produced **B1** below. Out of scope for this task; recommended as a follow-up.

---

## 3. Acceptance Matrix

The parent task record carries no explicit `acceptance` array, so A1–A5 are
derived from the commit message's own claims.

### Implemented and covered

| Ref | Acceptance rule | Evidence | Result |
|---|---|---|---|
| **A1** | The nanoid advisory `GHSA-2v37-7h3g-55p8` no longer trips the gate | §4.2 — gate exits `1` at base naming nanoid, `0` at approved head | **PASS** |
| **A2** | The change pins the defect rather than a path that already passed | §4.2 — the pre-fix run is a real reproduction at `1c2c061a`, not an inference | **PASS** |
| **A3** | postcss moved to a non-vulnerable version without a breaking `next` upgrade | §4.3 — `test_postcss_advisory_resolved` passes; `next` untouched in the diff | **PASS** |
| **A4** | The regenerated SBOM matches the active lockfiles | §4.3 — `test_sbom_and_provenance_present_and_valid` passes; negative twin also passes | **PASS** |
| **A5** | No collateral regression in the supply-chain suite | §4.3 — 9 passed; 4 failures are `uv`-missing environmental, unrelated to the diff | **PASS** |

### Blocking findings — closeout provenance

| Ref | Finding | Observed | Severity / disposition |
|---|---|---|---|
| **B1** | **`approved_head` `e497a465` is not on `dev`; the identical content is, as `0e14ff41`, delivered by an unrelated PR.** `git merge-base --is-ancestor e497a465 origin/dev` → **false**; same check for `0e14ff41` → **true**. The two commits have byte-identical diffs (§4.4) and identical author/timestamp (`CodexCoordinator`, 07:08:09Z) but different parents — the fix was authored twice, once on `fix/npm-audit-nanoid` and once directly on `task/ODP-CAP-FEATURE-FLAG-UI-001` to unblock *that* lane's own red audit gate. PR #668 merged at **08:42:34Z**; PR #693 was opened at **08:43:23Z**, i.e. **49 seconds after its own content was already on `dev`**. | `done` invokes `require_merged_pr`, which demands `pr_state == MERGED and pr_head == approved_head and pr_head_name == branch and merge_commit_on_target` (`scripts/ai_status.py:1955-2000`). PR #693 is OPEN. **`done` raises `SystemExit` today.** | **Blocking closeout.** Not a code defect — the vulnerability *is* fixed on `dev`. Purely a provenance mismatch. |
| **B2** | **Duplicate open PR #692 on a non-task branch** (`fix/npm-audit-nanoid`) pointing at the same `e497a465`, with no auto-merge armed and no task registration. It shares #693's check-runs because check-runs attach to the SHA, so it *displays* a `task-review-gate` pass it did not independently earn. | Two OPEN PRs, same head SHA, same base, both `BLOCKED`. | **Should be closed, and closed *second*.** See the ordering warning below. |
| **B3** | **Neither `e497a465` nor `0e14ff41` carries `LLM-Agent` / `Task-ID` / `Reviewer` trailers.** Both bodies end at `Co-Authored-By:`. The subject `security: bump nanoid past GHSA-2v37-7h3g-55p8` is not on the `.githooks/commit-msg` exemption list (`Merge `, `Revert `, `promote:`, `hotfix:`, `publish:`, `OPS-*`), so the hook should have rejected it — meaning the hook was bypassed or not installed. | Consequence: the commit that actually delivered the fix to `dev` has **no machine-readable link to `ODP-SEC-NPM-AUDIT-NANOID-001`**. Anyone auditing `dev` for this task's delivery finds nothing. | **Non-blocking for `done`** (the gate checks ancestry, not trailers) but it is the reason B1 is hard to see from the task record alone. Worth a `scripts/git/install_hooks.sh` check on the authoring lane. |

### Recovery path — recommended, in order

1. **Let PR #693 merge.** Its content diff against `dev` is empty (§4.4), so the
   merge is a no-op to the tree — but it is the *only* action that makes
   `e497a465` an ancestor of `dev` and satisfies `require_merged_pr`. Auto-merge
   is already armed with method `MERGE`, and PR #668 demonstrates the merge queue
   preserves original commit SHAs rather than rewriting them (`956170de` retains
   `0e14ff41` and `41cc5631` as ancestors), so `e497a465` will survive the queue
   intact. It only needs `product` to finish; that check already passed on this
   exact SHA at 07:09:18Z.
2. **Then close PR #692** as a duplicate.
3. **Then** run `done` on the parent.

**Do not close PR #693 as redundant.** It is tempting — the content is already
on `dev` — but closing it strands `ODP-SEC-NPM-AUDIT-NANOID-001` in
`review_approved` with no path to `done`, since no future commit will ever make
`e497a465` an ancestor of `dev`.

**Ordering warning on B2.** Close #692 *after* #693 merges, not before, and do
not merge #692 as a shortcut. Both PRs share head SHA `e497a465` but have
different head *branch names*, and `pull_request_status_for_branch` looks up the
PR **for the task branch**. If #692 merged first, `e497a465` would become an
ancestor of `dev`, but the PR the gate inspects (#693 on the task branch) would
be auto-closed by GitHub with ambiguous `state`/`mergedAt`/`mergeCommit` fields —
and `require_merged_pr` fails closed on all three. That turns a one-check wait
into an unrecoverable state. The deterministic path is: merge #693, close #692.

**If PR #693 cannot be merged at all**, the fallback is a fresh commit on
`task/ODP-SEC-NPM-AUDIT-NANOID-001` carrying the correct trailers (fixing B3 at
the same time) and re-approval to move `approved_head` onto a commit that can
reach `dev`. That is strictly worse than option 1 and should not be attempted
while #693 is merely waiting on a check.

---

## 4. Verification — commands run and observed output

All commands run 2026-08-08 by `Claude3`. Pre-fix runs used a throwaway worktree
created with `git worktree add --detach /tmp/nanoid-verify-prefix 1c2c061a` and
removed afterwards; `git status --short` was confirmed empty at the end (§4.7).

### 4.1 Heads under test

```
$ git log --oneline -1 e497a465
e497a465 security: bump nanoid past GHSA-2v37-7h3g-55p8      # approved_head, PR #693 + #692

$ git log --format="%H %P" -1 e497a465
e497a465... 1c2c061a...                                       # base = 1c2c061a

$ git log --format="%H %P" -1 0e14ff41
0e14ff41... b4d0a9d6...                                       # the copy that actually reached dev
```

### 4.2 The decisive regression check — gate red at base, green at head

This is the core evidence in this packet. The exact command the gate runs, at
the parent's base commit:

```
$ cd /tmp/nanoid-verify-prefix && git rev-parse HEAD
1c2c061a7d6f6f3973a455b502390eaf03c76da0

$ grep -A2 '"node_modules/nanoid"' package-lock.json
      "version": "3.3.16",

$ npm audit --omit=dev --audit-level=high
PREFIX_GATE_EXIT=1
# npm audit report

nanoid  <3.3.17
Severity: high
nanoid: custom generators can loop indefinitely when size is zero - https://github.com/advisories/GHSA-2v37-7h3g-55p8
fix available via `npm audit fix`
node_modules/nanoid

postcss  <=8.5.22
Severity: moderate
PostCSS: incomplete fix of GHSA-6g55-p6wh-862q — attacker-controlled sourceMappingURL
reads arbitrary .map files when `from` is unset - https://github.com/advisories/GHSA-fxqj-rqcc-2cmp
fix available via `npm audit fix --force`
Will install next@16.3.0, which is a breaking change
node_modules/postcss
  next  9.3.4-canary.0 - 16.3.0-preview.10

3 vulnerabilities (2 moderate, 1 high)
```

At the approved head's content (present on `dev`, and in this sidecar worktree):

```
$ grep -A2 '"node_modules/nanoid"' package-lock.json
      "version": "3.3.18",

$ npm audit --omit=dev --audit-level=high
GATE_EXIT=0
found 0 vulnerabilities
```

**Exit `1` → exit `0`, with the advisory named in the failure output.** The
`--force`/`next@16.3.0` warning in the pre-fix output is the evidence for §2's
claim that the postcss moderate was cleared *without* taking the breaking path.

### 4.3 Supply-chain suite at the approved head

```
$ python3 -m pytest tests/security/test_supply_chain_security_gate.py -p no:randomly
4 failed, 9 passed in 7.94s

FAILED test_pip_audit_passes
FAILED test_sast_scan_passes
FAILED test_stale_lockfiles_rejected_negative
FAILED test_vulnerable_fixtures_rejected_negative
```

The 4 failures are **environmental**, not caused by the parent diff — all four
shell out to `uv`, which is absent here:

```
E   FileNotFoundError: [Errno 2] No such file or directory: 'uv'

$ for b in uv ruff bandit semgrep pip-audit; do command -v $b || echo "$b NOT FOUND"; done
uv NOT FOUND / ruff NOT FOUND / bandit NOT FOUND / semgrep NOT FOUND / pip-audit NOT FOUND
```

The four tests that actually bear on this diff all pass:

```
$ python3 -m pytest \
    tests/security/test_supply_chain_security_gate.py::test_npm_audit_passes \
    tests/security/test_supply_chain_security_gate.py::test_sbom_and_provenance_present_and_valid \
    tests/security/test_supply_chain_security_gate.py::test_postcss_advisory_resolved \
    tests/security/test_supply_chain_security_gate.py::test_invalid_provenance_rejected_negative \
    -p no:randomly
4 passed in 1.27s
```

`test_sbom_and_provenance_present_and_valid` re-runs `generate_sbom()` and
compares against the committed file, so its pass is direct evidence for **A4**;
`test_invalid_provenance_rejected_negative` confirms that comparison is
fail-closed rather than vacuous. The parent's claimed "13 passed" is consistent —
13 tests exist, and the 4 shortfalls here are toolchain absence. CI runs this
file with the toolchain installed, and `orchestrator` is green on PR #693.

SBOM content matches the lockfile at `dev`:

```
$ git show origin/dev:docs/evidence/completion/ODP-PGAP-SUPPLY-001/sbom.json | grep -i 'nanoid\|postcss'
      "purl": "pkg:npm/nanoid@3.3.18",
      "purl": "pkg:npm/postcss@8.5.26",
```

### 4.4 The B1 provenance evidence

```
$ git merge-base --is-ancestor e497a465 origin/dev && echo ANCESTOR || echo "NOT ancestor"
NOT ancestor                     # <-- approved_head is not on dev

$ git merge-base --is-ancestor 0e14ff41 origin/dev && echo ANCESTOR || echo "NOT ancestor"
ANCESTOR                         # <-- but an identical-content twin is
```

The two commits are byte-identical in effect:

```
$ diff <(git show e497a465 --format="") <(git show 0e14ff41 --format="")
IDENTICAL DIFF
```

And `dev` already carries the fixed content, so PR #693's *remaining* change is
empty:

```
$ git diff --stat origin/dev e497a465 -- package-lock.json \
    docs/evidence/completion/ODP-PGAP-SUPPLY-001/sbom.json
(no output — dev already holds identical content for both files)

$ git show origin/dev:package-lock.json | grep -A1 '"node_modules/nanoid"'
      "version": "3.3.18",
$ git show origin/dev:package-lock.json | grep -A1 '"node_modules/postcss"'
      "version": "8.5.26",
```

How `0e14ff41` reached `dev`:

```
$ git log --oneline --ancestry-path 0e14ff41..origin/dev
41cc5631 ODP-CAP-FEATURE-FLAG-UI-001: drop the duplicate feature flag route page
956170de Merge pull request #668 from alfloop-dev/task/ODP-CAP-FEATURE-FLAG-UI-001

$ git branch -a --contains 0e14ff41
  task/ODP-CAP-FEATURE-FLAG-UI-001
  remotes/origin/dev
  remotes/origin/gh-readonly-queue/dev/pr-668-1c2c061a...

$ gh pr view 668 --json mergedAt,mergeCommit
{"mergedAt":"2026-08-08T08:42:34Z","mergeCommit":{"oid":"956170de..."}}
```

Note the merge-queue ref `gh-readonly-queue/dev/pr-668-1c2c061a` and that
`956170de` retains `0e14ff41` as an ancestor — the queue preserved the original
SHAs rather than rewriting them. This is the evidence for §3's claim that
`e497a465` will survive PR #693's own trip through the queue.

Trailer check for **B3**:

```
$ git log -1 --format="%B" e497a465 | tail -3
tests/security/test_supply_chain_security_gate.py: 13 passed.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
```

No `LLM-Agent`, no `Task-ID`, no `Reviewer`, on either commit.

### 4.5 Advisories outside the gate's scope (observation 4)

Same tree, gate command vs. unrestricted:

```
$ npm audit --omit=dev --audit-level=high   ->  exit 0,  found 0 vulnerabilities
$ npm audit --audit-level=high              ->  exit 1,  2 high severity vulnerabilities
$ npm audit                                 ->  exit 1,  2 high severity vulnerabilities

brace-expansion  <=1.1.17 || 4.0.0 - 5.0.8   high   GHSA-mh99-v99m-4gvg, GHSA-rgw5-rvv9-x895
js-yaml          4.0.0 - 4.3.0               high   GHSA-5p4m-2wfm-xmqj

$ npm audit --json | ... 'name severity isDirect nodes'
brace-expansion high isDirect=False nodes=['node_modules/@typescript-eslint/typescript-estree/node_modules/brace-expansion', 'node_modules/brace-expansion']
js-yaml         high isDirect=False nodes=['node_modules/js-yaml']

$ grep -c nanoid <full audit output>
0                                            # nanoid is gone from the report entirely
```

Both are dev-tree only and therefore correctly outside `--omit=dev`. **The gate
is genuinely green.** This is recorded as an **O1** precision note on the commit
message's "npm audit now reports 0 vulnerabilities" — true for the gate command,
not for `npm audit` unqualified — and as forward-looking context for observation 4.

### 4.6 PR / check-run state

```
$ gh api repos/alfloop-dev/odayplus/commits/e497a465/check-runs
product           in_progress  null      2026-08-08T08:43:29Z   # PR #693's run
orchestrator      completed    success   2026-08-08T08:43:29Z
performance-gate  completed    success   2026-08-08T08:43:29Z
product-e2e-gate  completed    success   2026-08-08T08:43:29Z
performance-gate  completed    success   2026-08-08T07:09:18Z   # PR #692's earlier run
product-e2e-gate  completed    success   2026-08-08T07:09:24Z
product           completed    success   2026-08-08T07:09:18Z   # <-- product already passed on this SHA
orchestrator      completed    success   2026-08-08T07:09:18Z
```

`product` has already completed `success` on `e497a465`. The current
`in_progress` is a re-run, which is why §3 recommends waiting rather than
intervening.

### 4.7 Worktree hygiene

```
$ git status --short
(empty)

$ git worktree remove /tmp/nanoid-verify-prefix --force
(removed)
```

`test_generated_client_drift_rejected_negative` mutates
`packages/openapi-client/src/index.ts` and restores it in a `finally` block; the
empty `git status` above confirms it restored cleanly and left no drift fixture
behind.

### 4.8 Not run

- **`uv`-dependent gates** — `pip-audit`, SAST, and the two `uv lock` negative
  tests could not run (§4.3). CI is the authority; `orchestrator` is green on
  PR #693.
- **`ruff` / `bandit` / `semgrep`** — not installed in this environment. Not
  relevant to a lockfile-only diff.
- **`npm install` / runtime exercise of nanoid 3.3.18** — the change is
  `--package-lock-only`; no `node_modules` tree was materialised and no
  application code path was executed. Version resolution and audit outcome are
  the appropriate evidence for this class of change.
- **The `product` CI job** — still `in_progress` on PR #693 at the time of
  writing (though already `success` on the same SHA from the 07:09 run).

---

## 5. Handoff Note

- **This packet's scope**: support artifact only. The single file touched by this
  sidecar task is
  `support/sidecars/ODP-SEC-NPM-AUDIT-NANOID-001/ODP-SEC-NPM-AUDIT-NANOID-001-SIDECAR-REVIEW.md`.
  No canonical truth, contract, runtime, registry, or governance file is modified.

- **Base advance**: none. The sidecar branch was opened from `origin/dev` tip
  (`956170de`) and was `0 behind / 0 ahead` when work started.

- **Sidecar reviewer `CodexCoordinator`** — the three claims worth checking
  hardest, in order:
  1. **B1** (§3, §4.4). It is the only finding that changes what the parent owner
     must do next, and it contradicts the natural reading of the task record
     ("approved, so just close it"). The two `merge-base --is-ancestor` results
     and the empty two-dot diff are the whole argument; please confirm you read
     `scripts/ai_status.py:1955-2000` as requiring `pr_head == approved_head` on
     a **merged** PR, because if that gate is laxer than I read it, B1 dissolves.
  2. **The ordering warning on B2** (§3). My claim that merging #692 first would
     leave #693 in a state `require_merged_pr` fails closed on is reasoned from
     the code path plus GitHub's auto-close behaviour for shared head SHAs — it
     is **not** something I reproduced, and I would not want it tested in
     production. If you disagree, the disagreement is cheap to settle by simply
     following the recommended order anyway.
  3. **§4.2's pre-fix reproduction.** This is the packet's strongest evidence and
     the only claim that could be undermined by a moving advisory database — a
     re-run days from now may show a *different* set of findings at the base
     commit. The nanoid line specifically should still be there; if you re-run
     and it is not, tell me, because that would mean the advisory was withdrawn.

  Everything in §3's acceptance matrix (A1–A5) is backed by a command in §4 with
  its observed output quoted.

- **Parent action**: the parent is `review_approved` with `task-review-gate`
  already reporting approval by `Claude`, so this packet is a record and a
  closeout-unblocking analysis, not a gate. Parent owner `CodexCoordinator`
  decides whether to absorb it. My recommendation (§3): wait for `product` on
  PR #693 → let auto-merge take it → close PR #692 → run `done`. Then, as a
  separate follow-up, observation 2 (a `test_nanoid_advisory_resolved` version
  assertion mirroring the postcss one) and observation 4 (a scheduled audit job
  so advisory drift arrives as a dated alert rather than as a red check on an
  unrelated PR).

- **Blocking what**: no finding in this packet blocks the *parent's approval* —
  the fix is correct and the vulnerability is objectively resolved on `dev`.
  **B1 blocks the parent's `done`**, and will keep blocking it until PR #693
  merges. That is the one thing to carry out of this packet.

---

## 6. Round 2 — outcome verification (2026-08-17)

Round 1 (§1–§5) was written 2026-08-08T08:57Z and is preserved above unedited.
This section records what actually happened to its findings, re-runs the two
claims that could decay with time, and states what is left.

All round-2 commands were run 2026-08-17 by `Claude` in the sidecar worktree
after composing the current base — `origin/dev` (`3ad0b503`) merged into
`task/ODP-SEC-NPM-AUDIT-NANOID-001-SIDECAR-REVIEW`, 780 commits, no conflicts.

### 6.1 B1 — RESOLVED. The recommended recovery was carried out.

```
$ gh pr view 693 --json state,mergedAt,mergeCommit,headRefOid
state       MERGED
mergedAt    2026-08-08T09:34:01Z
headRefOid  e497a46551d96b2e1163493a5f45284731b6100c
mergeCommit 7c21c070ec7aa9afd70a6a1a42516e0a3bf73373

$ git merge-base --is-ancestor e497a465 origin/dev  &&  echo yes
yes                                    # round 1 observed: NOT an ancestor
$ git merge-base --is-ancestor 7c21c070 origin/dev  &&  echo yes
yes
```

PR #693 merged **37 minutes after this packet was written**, with `e497a465`
preserved as an ancestor rather than rewritten — exactly as §3 predicted from the
PR #668 precedent. `require_merged_pr`'s three conditions (`state == MERGED`,
`pr_head == approved_head`, merge commit on target) are all satisfiable for the
parent today. B1 is closed and needs no further action.

### 6.2 B2 — RESOLVED, but the ordering played out differently than recommended.

```
$ gh pr view 692 --json state,closedAt,mergedAt
state     CLOSED
closedAt  2026-08-08T09:01:26Z
mergedAt  null
```

§3 said "close #692 **after** #693 merges". In practice #692 was closed at
09:01:26Z, **32m35s before** #693 merged at 09:34:01Z — the reverse order. No
harm resulted, and the round-1 warning was not wrong so much as imprecisely
scoped: the hazard it describes requires #692 to be **merged** first, which is
what makes GitHub auto-close #693 and leaves `require_merged_pr` reading
ambiguous `state`/`mergedAt`/`mergeCommit` fields. Closing a PR *without*
merging it touches neither the shared SHA's status nor #693's head branch, so
that direction is order-independent. **Correction of emphasis for future
readers: the ordering constraint is on merging, not on closing.**

### 6.3 A1 re-verified at the composed base — the advisory-drift caveat cleared.

§5 flagged that §4.2's evidence is the one claim a moving advisory database could
undermine. Re-run on the current tree:

```
$ grep -A2 '"node_modules/nanoid"' package-lock.json
      "version": "3.3.18",

$ npm audit --omit=dev --audit-level=high
found 0 vulnerabilities
EXIT=0
```

Nine days and 780 `dev` commits later the gate is still green on the production
dependency tree, and no new high-severity advisory has appeared. The *pre-fix*
half of §4.2 (exit `1` at `1c2c061a` naming `GHSA-2v37-7h3g-55p8`) was **not**
re-run — it needs a throwaway detached worktree, and re-running it would test the
advisory database rather than the parent's change, which is already merged.

### 6.4 A5 re-verified — §4.3's four failures were environmental, as claimed.

```
$ uv run -p 3.12 python -m pytest tests/security/test_supply_chain_security_gate.py -p no:randomly -q
.............                       [100%]
13 passed
```

Round 1 reported `9 passed, 4 failed` and attributed all four failures to `uv`
being absent (`test_pip_audit_passes`, `test_sast_scan_passes`, and the two
`uv lock` negative twins). With `uv` present the suite is fully green, which
confirms that attribution rather than merely asserting it. It also matches the
parent commit `e497a465`'s own message body, which claims
`test_supply_chain_security_gate.py: 13 passed`.

### 6.5 B3 — still open, still non-blocking.

```
$ git log -1 --format=%B e497a465 | tail -2
Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
$ git log -1 --format=%B 0e14ff41 | tail -2
Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
```

Neither delivering commit gained `LLM-Agent` / `Task-ID` / `Reviewer` trailers.
Both are merged and must not be rewritten, so B3 is now permanent history. It
remains non-blocking (the gate checks ancestry, not trailers). The forward-looking
half of B3 stands: the authoring lane bypassed or had not installed
`.githooks/commit-msg`, and a hook-installation check there is still worth doing.

### 6.6 Parent task record

`ODP-SEC-NPM-AUDIT-NANOID-001` is no longer present in `ai-status.json` or
`current-work.md`. This packet cannot therefore state whether it reached `done`
through `scripts/ai-status.sh` or was dropped from the board by the
`github-pr-reimport-2026-08-17` pass that also rewrote this sidecar's own record.
What *is* durably auditable is the delivery itself: PR #693's merge commit
`7c21c070` carries `e497a465` onto `dev`, and §6.3 shows the vulnerability
resolved in the current tree. Flagging the gap rather than guessing at it — if
the parent needs a board record, that is a separate task, not this sidecar's.

### 6.7 Base advance

§5's round-1 note "Base advance: none" is superseded. This branch had fallen 780
commits behind `origin/dev`; the base was composed by merge (not rebase), so
`5d889a94` — the head the reviewer approved in round 1 — survives as an ancestor
of the new head rather than being rewritten. `npm audit` (§6.3) and the
supply-chain suite (§6.4) were both run *after* the merge, so this packet's
surviving claims are verified against the composed tree, not the stale one.

**Second composition (2026-08-17, round 2b).** `origin/dev` advanced a further
14 commits to `078ed156` while PR #694 was open. Composed again by merge, no
conflicts. `package-lock.json` is byte-identical across `3ad0b503..078ed156`, so
§6.3's evidence is not merely re-asserted — the artifact it measures did not
move. `npm audit --omit=dev --audit-level=high` was nonetheless re-run on the
newly composed tree and still reports `found 0 vulnerabilities`, `EXIT=0`.

### 6.9 CI on PR #694 — the round-2 re-review failure was environmental

The `orchestrator` check on PR #694 failed four times (2026-08-17 15:29Z–16:49Z),
which requeued this task for CI repair. The failure is not in this branch's
content — it is GitHub's action-download layer rate-limiting the runner:

```
Download action repository 'astral-sh/setup-uv@v5' (SHA:d4b2f3b6…)
##[warning]Failed to download action … Error: Response status code does not
indicate success: 429 (Too Many Requests).
##[warning]Back off 25.863 seconds before retry.
… (3 attempts) …
##[error]Failed to download archive '…/astral-sh/setup-uv/tar.gz/…' after 3 attempts.
```

The job never reached a step that executes repository code. Every other check on
the same head — `change-scope`, `boundary`, `classify`, `product`,
`performance-gate`, `product-e2e-gate` — passed. The only diff this branch
carries against `dev` is this Markdown file, which no CI job parses or executes,
so no content change could have caused or can repair the failure. The repair is
a re-run on the re-composed head; if `setup-uv` 429s recur across unrelated PRs,
pinning or vendoring that action is a platform task, not a sidecar one.

### 6.8 What is left

Nothing in this packet blocks anything. B1 and B2 are resolved by events, A1 and
A5 re-verified, B3 is closed as unfixable-in-place. The two round-1 follow-up
observations remain unclaimed and are still worth separate tasks: a
`test_nanoid_advisory_resolved` version assertion mirroring the postcss one
(observation 2), and a scheduled audit job so advisory drift arrives as a dated
alert instead of a red check on an unrelated PR (observation 4) — which is
precisely the failure mode that produced the duplicate-authoring mess in B1.

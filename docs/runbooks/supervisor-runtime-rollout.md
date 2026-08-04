# Runbook: Supervisor Runtime Rollout

**Purpose**: Safe procedure for updating the Pantheon supervisor runtime checkout to the
latest `dev` tip and verifying the new code is live.

**Applies to**: Anyone operating the live Pantheon supervisor process (typically the
infrastructure team or an on-call engineer).

---

## Background

The Pantheon supervisor runs from an **independent git checkout** (the live runtime root)
that is separate from every worker worktree. Merging work into `dev` does **not**
automatically update the running supervisor. The runtime must be explicitly pulled forward.

**2026-08-04 incident**: The live runtime was 126 commits behind `dev` for several days.
All orchestrator fixes merged during that period were invisible in production. A freshness
check (`scripts/supervisor_runtime_health.py --check-git-freshness`) now makes this drift
detectable. This runbook specifies the update procedure.

---

## When to run this runbook

- Routine post-merge rollout for orchestrator or supervisor changes.
- On-call response when `runtime_git_not_behind` or `runtime_git_not_detached` checks fail.
- After a `PANTHEON_STATUS_ROOT` re-point or a worktree rebuild.

---

## Pre-flight checks

Before updating, confirm the current state:

```bash
# 1. Record the current HEAD of the runtime checkout.
git -C "$PANTHEON_STATUS_ROOT" rev-parse HEAD

# 2. Check how far behind the runtime is.
git -C "$PANTHEON_STATUS_ROOT" fetch origin
git -C "$PANTHEON_STATUS_ROOT" rev-list --count HEAD..origin/dev

# 3. Run the health check to capture a pre-update baseline.
python3 "$PANTHEON_STATUS_ROOT/scripts/supervisor_runtime_health.py" \
  --repo "$PANTHEON_STATUS_ROOT" \
  --check-git-freshness \
  --json | tee /tmp/runtime-health-pre.json

# 4. Confirm the supervisor is alive (or intentionally stopped).
cat "$PANTHEON_STATUS_ROOT/.orchestrator/state.json" | \
  python3 -c "import json,sys; s=json.load(sys.stdin); print(s.get('supervisor',{}).get('lifecycle'))"
```

> [!WARNING]
> Do **not** proceed if the supervisor is in `degraded` lifecycle without understanding
> why. A stale runtime may be masking an active crash loop; rolling forward could
> surface new failures without fixing root cause.

---

## Step 1 — Stop the supervisor (graceful)

If the supervisor is running, signal a graceful shutdown before pulling:

```bash
# Option A: via the watchdog / systemd unit (preferred)
sudo systemctl stop pantheon-supervisor

# Option B: send SIGTERM to the PID if running manually
PID=$(cat "$PANTHEON_STATUS_ROOT/.orchestrator/supervisor.pid" 2>/dev/null)
[ -n "$PID" ] && kill -TERM "$PID"

# Wait for the process to exit (up to 30 s)
for i in $(seq 1 30); do
  [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null || break
  sleep 1
done
```

> [!NOTE]
> Worker worktrees and the task queue survive a supervisor restart. In-progress workers
> continue to run; they will reconnect when the supervisor comes back.

---

## Step 2 — Pull the runtime checkout forward

```bash
cd "$PANTHEON_STATUS_ROOT"

# Fetch latest
git fetch origin

# Confirm not in detached HEAD state before pulling
git symbolic-ref --quiet HEAD || {
  echo "ERROR: runtime checkout is in detached HEAD — manual recovery required."
  exit 1
}

# Fast-forward to dev tip
git merge --ff-only origin/dev

# Verify
git log --oneline -5
```

> [!CAUTION]
> `git merge --ff-only` will abort if the runtime has local commits not in `origin/dev`.
> Do **not** use `git reset --hard` without understanding the divergence. Investigate and
> resolve before proceeding.

---

## Step 3 — Verify the updated checkout

```bash
# 1. Runtime is at expected commit.
git -C "$PANTHEON_STATUS_ROOT" rev-parse HEAD

# 2. No commits behind dev.
python3 "$PANTHEON_STATUS_ROOT/scripts/supervisor_runtime_health.py" \
  --repo "$PANTHEON_STATUS_ROOT" \
  --check-git-freshness \
  --json | python3 -c "
import json, sys
r = json.load(sys.stdin)
gf = r.get('git_freshness') or {}
print('commits_behind:', gf.get('commits_behind'))
print('detached_head:', gf.get('detached_head'))
for c in r.get('checks', []):
    if c['name'].startswith('runtime_git'):
        print(c['name'], '->', 'OK' if c['ok'] else 'FAIL')
"
```

Expected output:

```
commits_behind: 0
detached_head: False
runtime_git_not_detached -> OK
runtime_git_not_behind -> OK
```

---

## Step 4 — Restart the supervisor

```bash
# Option A: via systemd
sudo systemctl start pantheon-supervisor

# Option B: direct (for manual / dev setups)
cd "$PANTHEON_STATUS_ROOT"
nohup python3 .orchestrator/supervisor.py > logs/supervisor.log 2>&1 &
echo $! > .orchestrator/supervisor.pid
```

---

## Step 5 — Post-restart health check

Wait ~60 s for the supervisor to complete its first loop, then:

```bash
python3 "$PANTHEON_STATUS_ROOT/scripts/supervisor_runtime_health.py" \
  --repo "$PANTHEON_STATUS_ROOT" \
  --check-git-freshness \
  --json | tee /tmp/runtime-health-post.json

python3 - <<'EOF'
import json
with open("/tmp/runtime-health-post.json") as f:
    r = json.load(f)
print("healthy:", r["healthy"])
for c in r["checks"]:
    if not c["ok"]:
        print("FAIL:", c["name"], c)
EOF
```

All checks should be `ok`. If `supervisor_heartbeat_fresh` is failing, the supervisor
may not have written its first heartbeat yet — wait another 60 s and retry.

---

## Rollback

If the updated runtime causes regressions:

```bash
cd "$PANTHEON_STATUS_ROOT"

# Record the bad HEAD for the post-mortem.
git rev-parse HEAD > /tmp/bad-runtime-head.txt

# Reset to the commit that was running before.
PREV_HEAD=$(python3 -c "import json; print(json.load(open('/tmp/runtime-health-pre.json')))")
# ... or use git reflog:
git reflog | head -5

# Fast-forward to the desired commit (never reset --hard without understanding).
git checkout <previous-good-sha>
```

Then restart the supervisor and confirm health (Steps 4–5).

---

## Scheduling / Automation guidance

To prevent silent drift, wire `--check-git-freshness` into your existing monitoring:

```bash
# Example cron / Makefile target (runs every 5 minutes)
*/5 * * * * python3 "$PANTHEON_STATUS_ROOT/scripts/supervisor_runtime_health.py" \
  --repo "$PANTHEON_STATUS_ROOT" \
  --check-git-freshness \
  --json >> /var/log/supervisor-health.jsonl

# Alert if exit code != 0
```

Or integrate into the watchdog via `scripts/supervisor_watchdog.py` using
`--require-git-freshness` (future enhancement tracked separately).

---

## Detached HEAD recovery

If the runtime is in detached HEAD state (symbolic-ref fails):

```bash
cd "$PANTHEON_STATUS_ROOT"

# See what commit and what branches contain it
git log --oneline -3
git branch -a --contains HEAD | head -10

# Re-attach to the tracking branch
git checkout dev          # or main / whatever the primary branch is
git branch --set-upstream-to=origin/dev dev
git merge --ff-only origin/dev
```

Then run full health check (Step 5).

---

*Last updated: 2026-08-04 — ODP-ORCH-RUNTIME-ROLLOUT-PRECHECK-001*

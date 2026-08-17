# Account-pool scheduling

The dispatcher schedules credentials, not display names.  `Antigravity`,
`Antigravity2`, and similar names may be useful ownership roles, but they must
not be counted as independent workers if they use the same login.

Configure each real login once under `account_pools`; every logical agent and
every executable slot using that login receives the same `account_pool`.

```json
{
  "account_pools": {
    "antigravity_main": {"state": "healthy", "max_concurrent": 3},
    "codex_bjoe": {"state": "exhausted", "max_concurrent": 0, "reason": "quota exhausted"}
  },
  "agents": {
    "antigravity": {"account_pool": "antigravity_main"},
    "antigravity2": {"account_pool": "antigravity_main"},
    "antigravity_slot_1": {
      "account_pool": "antigravity_main",
      "dispatch_slot_for_pool": "antigravity_main"
    }
  }
}
```

Rules enforced by Supervisor:

- A pool's `max_concurrent` is the hard cap across every alias and slot.
- `state` values `disabled`, `exhausted`, `cooldown`, and `paused` prevent new
  dispatches without pretending the account has a live worker.
- `dispatch_slot_for_pool` lets all aliases share a finite set of executable
  processes. `max_tasks_per_agent` remains an ownership/load target, not a
  source of concurrency.
- Reviewers must belong to a different account pool from the owner. When the
  registered-idle helper is enabled, Supervisor reassigns an invalid same-pool
  reviewer to a viable independent pool.
- Queue order is task priority (`P0` through `P3`), then lifecycle action,
  then board order. A failed worktree preflight is terminal for that dispatch
  event, so it cannot reserve an account slot or starve following tasks.

Use a small initial cap and raise it only after the dashboard reports real
worker PIDs, healthy heartbeats, and no provider capacity failures. A
`manual_pending` record without a PID is a pending handoff, never active worker
capacity.

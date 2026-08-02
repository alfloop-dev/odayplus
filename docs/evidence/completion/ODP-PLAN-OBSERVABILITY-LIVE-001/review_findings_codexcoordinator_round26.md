# ODP-PLAN-OBSERVABILITY-LIVE-001 — Coordinator Review Round 26

- Reviewed head: `73ea25e50403aa189a66b02b6427d900c414c2f7`
- Reviewer: `CodexCoordinator`
- Verdict: `REOPEN`
- Release claim: `NO-GO`; no PR, rollout, or deployment is authorized.

## Full-packet verification

- `python3 -m pytest -q tests/reliability/test_runtime_observability.py`: pass
  (55 tests; one upstream Starlette deprecation warning).
- Ruff and `git diff --check`: pass.
- The Round 25 default-store, public test-factory, strict hash/SHA, and
  same-instance threaded replay mutations are closed.

## B26 — durable consume is not atomic across store instances/processes

`FileDeliveryAuthorityStore` owns a per-instance `threading.Lock`. Two store
instances pointed at the same production file therefore enter
`atomic_consume_if_valid` concurrently. They also write through the same fixed
`<store>.tmp` path. This does not provide the promised cross-process durable
replay boundary.

Exact-head mutation used two independent `FileDeliveryAuthorityStore` objects,
one shared authority file, one record, a two-party barrier inside the validator,
and concurrent consume calls. Expected: exactly one `DELIVERED`; the other call
must deterministically reject replay. Actual: both calls passed the unconsumed
read and raced the shared temp file; one crashed:

```text
FileNotFoundError: [Errno 2] No such file or directory:
'.../authority.tmp' -> '.../authority.json'
```

Depending on write ordering, the same missing shared lock can also allow both
callers to return `DELIVERED`. The existing concurrency test uses one store
instance, so its single `threading.Lock` does not exercise this production
boundary.

## Required complete-batch remediation

1. Protect read/validate/consume/write with an OS-visible lock shared by every
   instance and process using the authority store.
2. Use collision-free same-directory temporary files, flush and `fsync` file
   content before atomic replace, and durably sync the parent directory.
3. Treat corrupt/unreadable store state as an explicit fail-closed error; do
   not silently substitute an empty store during a transaction.
4. Add two-instance and multi-process races proving exactly one `DELIVERED`,
   deterministic replay rejection for every loser, no exception, valid JSON,
   and persistence after a fresh process/readback instance.
5. Re-run the complete Observability packet on one exact pushed head. Preserve
   the authentic-provider/Human-Ops evidence boundary and `NO-GO` release claim.

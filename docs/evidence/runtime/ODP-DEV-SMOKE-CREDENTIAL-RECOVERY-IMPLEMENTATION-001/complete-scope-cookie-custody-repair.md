# Complete-object retry/read scope and noncanonical-cookie custody

Author repair of rejected aa9cb0cc796d4cbbe0d3d0dfd1d3272aa593064b. Its17 matching
measurements passed (serial1164.441s/1200s), but all three fresh static advisories
(auth87/release24/intake44 complete bodies) returned CHANGES_REQUIRED. Never promote
these green tests over supported findings or erase prior d204/eb/51c failures.

Auth/intake findings: empty item lists bypass envelope and action authorization;
retry validates only one list and can requeue an out-of-scope whole stored record;
new replay test had data_owner/viewer without listing CREATE, so it refused before
replay. Release: missing HttpOnly or different SameSite erased a safe session value
from observation failure custody, allowing cleanup-completed while that value lived.

Unchanged-aa9 baseline: real loopback TLS observation issued a safely representable
cookie with each noncanonical attribute set, observation refused but owner cleanup
returned completed (two intended DID NOT RAISE). Corrected synthetic JWT/mounted
aliases/memory queue controls observed empty-list reads200/disclosure and denied
scope retries202 with status/version/payload mutation. Initial healthy retry fixture
mistakenly expected200 instead of the mounted202 contract; retained as invalid
fixture diagnostic, corrected and rerun against identical production bytes before
edits. Retrospective replay control now uses existing site_reviewer grant, spies on
ACTUAL enqueue return of seeded record, requires exact SCOPE_DENIED and no leaked
marker; otherwise-identical in-scope replay202 proves the intended branch. Both
strengthened replay controls pass unchanged aa9; old test did NOT prove this path.

Read/retry now share complete-object validation: both item lists must have valid
shapes; every object must satisfy all normalized axes/tenant; zero actual items
checks payload envelope, including explicit empty lists. Every path invokes its
role/action decision. Retry retains distinct mutation RBAC, staff ownership,
tenant, state/retry eligibility, version fence and audit. Healthy envelope/empty/
both-list retries still queue exactly one existing record and advance version;
foreign tenant/second-list/malformed scope denies without any state change. No
worker dispatch or downstream business effects claimed by memory tests.

Foreground transport independently retains safely sendable value before validating
cookie attributes; strict login acceptance unchanged. Private bounded observed-
cookie flag latches UNKNOWN even when an observed malformed value cannot be sent
safely or retained. Observations and recovery actions propagate custody/uncertainty
to attached owner. Tests require actual-TLS refusal, sticky cleanupUNKNOWN, exactly
one logout per retained A/B, and no second cleanup calls; unsafe value is never
sent but cannot become cleanup-completed. Existing bad-Domain login still refuses,
now asserts retained private cleanup-only value and redaction, not discarded value.
Initial post-repair old blank-inventory assertion failure retained before this
stronger expectation. No cookie canonical policy, crypto, budget, cleanup bound,
request cap, quarantine-first ordering, durable start or shared intent weakened.

Targeted new plus existing recovery/gate/composition/partial-batch controls pass.
Fresh immutable-head measurements and independent review still required; no
coverage/receipt transfer from aa9 or its opposite scopes. Offline fixtures only,
not live session/recovery/deployment or actual-PG proof for these cases. Original
owner/reviewer/churn/Human-Ops/PR1459 and ALL canonical/operational/F11 HOLDs remain.

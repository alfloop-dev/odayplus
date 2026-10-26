# Candidate-specific dev gate 4 decision

Candidate `355a94b52b14badc236be4b3e52eb936a7075549`, build `36313147910`, manifest `sha256:a1e3fcf6b765861dc269e3fd395b7bec6ac4b2b6f3b3207a3ffb623c6ca6dfaa`.

Result: **passed-with-deviation for dev only**, based on the current user's explicit confirmation of the two preceding scoped requests, transcribed in `user-authorization.json`. The associated release request expires `2026-09-27T22:18:13Z`; normal supervisor leases have their own shorter lifetime. No external identity or legal receipt is manufactured. The historical unsigned request remains unchanged as historical evidence.

Security measurements are unchanged from the reviewed build/preflight: candidate CI36311413006 product-security passed; build36313147910 production dependency audit has zero findings. The fresh exact-candidate full audit remains exit1 with 1 high js-yaml finding (GHSA-2883-xcg3-v3hh) and 2 moderate vitest/mocker findings (GHSA-82fw-gwwq-j7x9). Lockfile and relevant manifests are unchanged since the prior baseline. The new user decision expressly covers these known dev-tool findings on this candidate, instead of copying the old baseline-limited acceptance.

The automated OSS licence verdict remains FAIL with four LGPL cases requiring external H01 authority evidence. Existing operator conditions still apply: unmodified upstream binaries, dynamic linking/loading, NOTICE disclosure and source availability. The user approved the missing external receipt as a dev-only deviation; H01, proposed license policy and empty exemptions remain unchanged. Existing project-level Cloud SQL IAM-condition exceptions are disclosed rather than declared fixed.

The real read-only target diagnosis and two isolated full migration success receipts were independently reviewed in merged PR1374 (`4ac3f5b106091f197b8fe55831709994fe57a43e`). The target holds Dagster revision29b539ebc72a and no app schema; the reviewed fix isolates app history. No live migration, Cloud Run service rollout, runtime egress probe or IAM readback completion is claimed here. Parent rollout must capture those live receipts before done.

# Candidate a31 dev security decision

Candidate `a31e02ae391811a4c323ec4d834b70e200953366`, manifest `sha256:499110d08fc91eef448ba9e3697005b0978669946ca0065e065cb18871ca83b2`, Runtime Release build `36333397898`.

At `2026-09-27T22:55:45Z`, the user explicitly authorized dev admission and deployment under `HUMANOPS-DEV-MIGRATION-20260927T225545Z`, expiring `2026-09-28T04:55:45Z`. The complete scope is in `a31-user-authorization.json`. This is an interactive user decision transcribed by Codex, not an external identity signature or legal receipt.

The hosted build production npm audit is zero. The unchanged development lockfile has the same disclosed1 high js-yaml finding (GHSA-2883-xcg3-v3hh) and2 moderate Vitest/mocker findings (GHSA-82fw-gwwq-j7x9); no critical or new finding is included. The automated full dev audit remains failing and is not relabeled as passing. Four LGPL cases retain conditional-use obligations; external authoritative H01 evidence remains missing and is accepted only as a dev deviation. License policy remains proposed and exemptions remain empty.

Status: **passed-with-deviation for dev only**. Sixteen external sources remain disabled, credentials absent and default-deny egress required. No staging or production admission. Live egress/IAM readbacks remain rollout acceptance items. No deployment has occurred.

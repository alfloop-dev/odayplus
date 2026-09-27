# Gate 1 source and build evidence for rebuilt candidate

Candidate `a31e02ae391811a4c323ec4d834b70e200953366`, build `36333397898`, manifest `sha256:499110d08fc91eef448ba9e3697005b0978669946ca0065e065cb18871ca83b2`.

The hosted build succeeded, including source scans, production npm audit, deployment health/backup/restore/rollback rehearsal, and immutable image/signature/SBOM publication. It did not deploy.

Exact candidate CI `36329922612` succeeded under documentation scope. Its product jobs were **skipped**; this receipt does not claim those tests ran on a31. Full product CI `36311413006` passed on `355a94b52b14badc236be4b3e52eb936a7075549`. `a31-candidate-comparison.json` proves all 11 listed product/build Git objects identical, and the complete intervening diff is documentation. The inherited code/contract results are attributed to355; the fresh artifacts are attributed toa31. All three hosted run readbacks are included.

Gate 1 evidence is reconciled for the unchanged product/contract source plus the fresh exact-candidate build. Independent owner/reviewer adoption is still required. This is not a human authorization or live rollout receipt.

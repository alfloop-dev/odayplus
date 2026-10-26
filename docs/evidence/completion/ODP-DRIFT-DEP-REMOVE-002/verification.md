# Verification before publication

The isolated integration candidate passed 317 native-core, immutable golden,
data/feature/prediction/performance monitoring tests, plus the newly added
lock/installed/SBOM removal regression (1 test). Another 162 OSS execution,
ADR, SBOM isolation, licence/attestation, supply-chain and release-workflow
contract tests passed. The attached logs contain the pytest progress and
warnings. All historical golden files match PR #1218 byte-for-byte.

Commands:

- `python -m pytest tests/models/test_native_drift.py tests/models/test_evidently_monitor_baseline.py tests/models/test_evidently_monitor.py modules/learninghub/tests/test_prediction_drift.py modules/learninghub/tests/test_performance_drift_and_baseline_comparison.py -q` (317 tests, exit 0)
- `python -m pytest tests/models/test_evidently_monitor.py::test_native_monitoring_has_no_evidently_or_nltk_dependency -o addopts='' -q` (1 passed)
- `python -m pytest tests/integration/test_oss_ai_execution_flow.py tests/contract/test_deferred_oss_adr.py tests/tooling/test_generate_sbom_output_isolation.py tests/security/test_oss_license_gate.py tests/security/test_supply_chain_security_gate.py tests/ops/test_deploy_workflow_contract.py -q` (162 tests, exit 0)
- Ruff on changed Python files, code-boundary inventory validation, requirement-member validation, generated NOTICE check, and generated current SBOM check passed.

The actual dependency result comes from the separate complete 215-package
pip-audit receipt, not the historical `--local` unit-test path on dev.

Implementation files were compared byte-for-byte after transfer into the
Worker Manager task checkout. The prerequisite PRs still require their
ordinary review/merge flow; these results do not authorize merge or deployment.

`committed-audit.json` records a second complete 215-package scan at implementation commit `e18aa6948d4569ba8e37888e0e5291d18931048d`, using its own synchronized environment. `committed-inputs.json` binds the implementation files, generated current SBOM and NOTICE. This follow-up commit adds evidence and refreshes the SBOM source identity; it does not change the audited implementation or lock.


## Complete strict-gate integration preview

A separate isolated preview combines this cutover with every changed path in
PR #1188 at `24899e86f04a0b0e70a92e451317e17f93931b93` (including CI timeouts,
test wiring and the regenerated code-boundary inventory). It retains the
current SBOM path. `make security` completed with exit 0: **215 installed
Python dependencies audited with no vulnerabilities; 334 security tests
passed**, with 5 existing Starlette deprecation warnings. The 69 audit-boundary
tests also passed. Input and log hashes are in `final-integration-inputs.json`.
The native module's AST matches the formal cutover; only attribution comments
differ. These suites overlap earlier focused coverage and their counts should
not be added as if every test were unique.

Two setup failures are preserved for traceability: the first preview omitted
the updated supply-chain test file (327 passed, 1 failed), and the next omitted
CI timeout/inventory wiring (104 passed, 2 failed). After completing the
integration, the full security suite was rerun successfully. No gate behavior
was weakened to resolve those incomplete-assembly failures. The actual PRs
still require their own CI, review and merge records.


## Published-delivery preparation after prerequisite merges

PRs #1217, #1218 and #1219 have merged. Integration commit `ad709b8a05d3cd2905fc5d2edb04335b27a14e89`
composes the cutover with current dev, including the independently reviewed
comment-test timing repair. A fresh complete scan of its synchronized Python
3.12 environment passed with 215 dependencies and no vulnerabilities at
`2026-09-06T04:54:35.833754+00:00`. `integrated-audit.json` retains the exact source,
gate source, tool, commands and raw output hashes; `integrated-inputs.json`
binds implementation files and the regenerated active SBOM. SBOM and NOTICE
checks passed. Historical goldens and completion SBOMs remain unchanged.
This follow-up adds evidence and SBOM source identity only. The cutover PR
still requires its own CI and independent approval; it grants no deployment GO.

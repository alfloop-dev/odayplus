# ODP-MERGE-QUEUE-AUDIT-FAILURE-RECONCILIATION-001 Evidence

## 1. Context and Objective

- **Task ID**: `ODP-MERGE-QUEUE-AUDIT-FAILURE-RECONCILIATION-001`
- **Target Task**: `ODP-SUPPLY-CHAIN-LOCKFILE-CONSISTENCY-001` (PR #1183, commit `be9ded62f6982390909bdd1ef2d2229cd813976c`)
- **Objective**: Independently audit the latest merge queue failure receipt on PR #1183 (GitHub run `33856991396`). If the sole failure is `AUDIT UNAVAILABLE` (live npm audit timing out without advisories), use reviewer authority (`Antigravity3`) to reopen `ODP-SUPPLY-CHAIN-LOCKFILE-CONSISTENCY-001` to its owner (`Claude2`) with exact remediation constraints.

---

## 2. GitHub Run 33856991396 Exact Receipt Audit

GitHub run `33856991396` was triggered via `merge_group` for merge queue branch `gh-readonly-queue/dev/pr-1183-7feef7d3829f5557603d4dc74cc536819744ef06`.

### Job Results Summary

| Job | Status | Duration | Note |
|---|---|---|---|
| `orchestrator` | SUCCESS | 3m6s | Boundary inventory and orchestrator checks clean |
| `change-scope` | SUCCESS | 10s | Scope validation clean |
| `performance-gate` | SUCCESS | 1m8s | Performance gates passed |
| `product-e2e-gate` | SUCCESS | 13m54s | E2E gates passed |
| `product` | FAILURE | 32m55s | Single test failure in `test_npm_audit_passes` |

### Exact Failure Log in `product` (`Test product code`)

```text
=========================== short test summary info ============================
FAILED tests/security/test_supply_chain_security_gate.py::test_npm_audit_passes - AssertionError: npm audit gate failed with output:
  
  npm audit attempt 1/3 did not return advisory data: npm audit timed out after 300s
  npm audit attempt 2/3 did not return advisory data: npm audit timed out after 300s
  npm audit attempt 3/3 did not return advisory data: npm audit timed out after 300s
  AUDIT UNAVAILABLE: the npm registry never returned advisory data, so this run proves nothing about production dependencies. Last error: npm audit timed out after 300s
  
assert 2 == 0
 +  where 2 = CompletedProcess(args=['/home/runner/work/odayplus/odayplus/.venv/bin/python', '/home/runner/work/odayplus/odayplus/delivery_toolchain/security/npm_audit_gate.py'], returncode=2, ...).returncode
1 failed, 4878 passed, 23 skipped, 175 warnings, 16 subtests passed in 1703.09s (0:28:23)
```

---

## 3. Receipt Classification and Root Cause

1. **Sole Failure Point**:
   Out of 4,879 executed test cases in the product test suite, 4,878 passed. The sole failure was `tests/security/test_supply_chain_security_gate.py::test_npm_audit_passes`.
2. **Not a Security Vulnerability**:
   `delivery_toolchain/security/npm_audit_gate.py` returned `EXIT_AUDIT_UNAVAILABLE` (exit code `2`), rather than `EXIT_VULNERABLE` (exit code `1`).
3. **Not a Lockfile Defect**:
   The failure was caused by 3 consecutive timeout exceptions (`npm audit timed out after 300s`) against `registry.npmjs.org`. The local `package-lock.json` integrity and package tree were not rejected.
4. **Architectural Boundary Flaw**:
   Invoking live external registry network requests directly within a deterministic product test suite (`pytest`) introduces non-deterministic test flakiness and egress sensitivity into the merge queue.

---

## 4. Reconciliation and Reopen Action

As reviewer `Antigravity3`, executed `reopen` on `ODP-SUPPLY-CHAIN-LOCKFILE-CONSISTENCY-001` transferring ownership back to `Claude2`.

### Recorded Reopen Directive

```text
Review 退回：核對 GitHub run 33856991396（PR #1183 merge queue）exact receipt，唯一失敗為 tests/security/test_supply_chain_security_gate.py::test_npm_audit_passes 呼叫 live npm audit 時 registry 3 次連線超時（timed out after 300s）回傳 exit code 2 (AUDIT UNAVAILABLE)，其餘 4878 個測試及所有 job 皆成功通過。此 AUDIT UNAVAILABLE 為 registry 外部連線問題，並非 production 漏洞或 lockfile/package tree 缺陷。退回修正要求：(1) 保留 fail-closed 安全原則；(2) 停止在 deterministic product suite (pytest) 中直接呼叫 live audit，改由唯一 Runtime Release egress probe 執行並留下 receipt；(3) 嚴禁重試佇列、嚴禁放行 unavailable、嚴禁建立第二套 gate。
```

### Constraints Enforced

- **Fail-closed preservation**: Inability to contact the registry must never be treated as a pass.
- **Single egress probe boundary**: Live npm audit must execute only via Runtime Release egress probe and leave an auditable receipt, decoupled from unit/integration pytest runs.
- **No queue churn**: Merge queue was not retried and no unverified work was forced through.
- **No second gate**: Avoid creating divergent duplicate gates.

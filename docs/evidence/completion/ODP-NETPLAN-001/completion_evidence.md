# ODP-NETPLAN-001 — Completion Evidence

Integrate market context, feasibility and economics into NetPlan/OpsBoard.

- **Task ID:** `ODP-NETPLAN-001`
- **Owner:** `Claude2`
- **Reviewer:** `Antigravity4`
- **Phase:** `ODayPlus Decision`
- **Provides Contract:** `odayplus.netplan-emgi.v1` (version `1.0.0`, `decision_product`)
- **Requires Contracts:** `odayplus.sitescore-v3.v1`, `odayplus.physical-feasibility.v1`,
  `odayplus.site-economics.v1`, `emgi.site-market-context.v1`
- **Depends On:** `ODP-SITESCORE-V3-001`, `ODP-FEASIBILITY-001`, `ODP-ECONOMICS-001` (all `done`)
- **Source Ref:** `alfloop-dev/oday-data-platform@63e9c2fc5171c0e335f6465f5860704fe4dc4694`
  (`docs/design/emgi/v0.4.1/tasks/definitions/consumer-c.json`)

---

## Deliverables Summary

| Path | Description |
|---|---|
| `modules/netplan/integrations/emgi.py` | The single seam where NetPlan reads the EMGI decision stack. Defines contract `odayplus.netplan-emgi.v1` (`NetPlanEmgiDocument`, `NetPlanCandidateDecision`, `DecisionEvidence`, `EvidenceRef`, `validate_netplan_emgi_document`, `netplan_emgi_document_digest`), the versioned `NetPlanEmgiPolicy` that maps site economics onto solver inputs, and `NetPlanEmgiIntegrationService`, which admits a candidate site only when the market context and all three decision products agree on the same point-in-time manifest and market-context identity. |
| `modules/opsboard/integrations/emgi.py` | OpsBoard's human-approval and audit half: `OpsBoardEmgiApprovalService`, `NetPlanApprovalPacket`, `ApprovalActor`, `HumanApprovalRecord` and `AuditEvent`. Parks a validated plan in `PENDING_HUMAN_APPROVAL`, enforces the human-approver gate and the reject/return reason gate, and exports an audit-ready evidence bundle. |
| `tests/integration/test_netplan_emgi.py` | 46 integration tests covering the end-to-end path from the three real producers through admission, plan document validation, human approval and back into `build_scenario_options`. |
| `docs/evidence/completion/ODP-NETPLAN-001/completion_evidence.md` | This record. |

---

## Acceptance Verification

### 1. Use versioned market context and decision inputs and persist evidence/policy versions

Every candidate verdict is produced from explicitly versioned inputs:

- **Contract pinning.** Each decision document must declare the exact expected
  `contract_id` *and* `contract_version` (`odayplus.sitescore-v3.v1`,
  `odayplus.physical-feasibility.v1`, `odayplus.site-economics.v1`). Version drift is a
  `WITHHELD_PROVENANCE` verdict, not a coercion. The market context is pinned to
  `emgi.site-market-context.v1` via the released client constants, so the contract id and
  version are never re-declared as literals in this module.
- **Point-in-time manifest.** The evaluation `manifest_id` must appear in the site-scoped
  market context or at the context-document root, and the SiteScore v3 document's
  `manifest_id` must equal it. Sibling `contexts` are deliberately not traversed, so a
  manifest carried only by another site's context cannot vouch for this candidate.
- **Market-context identity.** The feasibility and economics documents must stamp a
  `source_market_context_id` equal to this site's `context_id`; a mismatch withholds.
- **Persisted evidence.** Every emitted `NetPlanCandidateDecision` carries a
  `DecisionEvidence` with the manifest id, the market-context id and digest, and one
  `EvidenceRef` per input document (`label`, `contract_id`, `contract_version`,
  `document_id`, `sha256`). The producer-declared digest wins; otherwise a canonical
  content hash of the payload is recorded.
- **Persisted policy versions.** `netplan_emgi_integration` (this module's
  `netplan-emgi-admission-v1`), `feasibility_gate` (the feasibility service's
  `physical-feasibility-gate-v1`), `economics_engine` (the simulator's `ENGINE_VERSION`),
  `sitescore_contract`, and `market_context_product` when the context publishes one.
  A missing feasibility policy version or economics engine version withholds the
  candidate, and `validate_netplan_emgi_document` rejects any candidate whose evidence
  carries no policy versions or no document refs.
- **Traceable solver inputs.** The evidence snapshot ids are attached to the emitted
  `CandidateSiteInput.source_snapshot_ids`, so the `ActionOption` the solver ultimately
  chooses still points back at the exact documents that justified it.

Fail-closed behaviour (no admission ever falls out of a default):

| Condition | Verdict |
|---|---|
| Missing/blank evaluation manifest, manifest mismatch, wrong site, contract or version drift, missing provenance stamp, missing policy version | `WITHHELD_PROVENANCE` |
| Feasibility `UNKNOWN_REQUIRES_SURVEY` / `CONDITIONAL`, economics `CONDITIONAL_GO` / `INVESTIGATE`, SiteScore not `READY`+`AVAILABLE`+`GO`, **any unrecognised decision value**, missing or non-finite economics figures, confidence score outside `[0, 1]` | `WITHHELD_INCOMPLETE` |
| Feasibility `INFEASIBLE`, economics `REJECT`, SiteScore `NO_GO` | `REJECTED` |

Broken provenance outranks a business rejection: when a document's chain of custody does
not hold, the site is reported as withheld rather than as a merits-based no, because the
rejection itself is not trustworthy either.

### 2. Keep final human approval and audit in OpsBoard

`OpsBoardEmgiApprovalService` owns the binding decision:

- `open_review()` validates the document against `odayplus.netplan-emgi.v1` before any
  state is persisted, pins the packet to the document digest, and copies the OpsBoard
  approval policy version plus every candidate's policy versions onto the packet.
- A service identity (`token_type == "service"` or a `service:`-prefixed subject) may open
  a review but can never decide one; an unauthenticated principal is refused separately.
- The approver must hold a NetPlan approver role (`executive`, `operations_manager`,
  `site_reviewer` by default, overridable per deployment).
- Rejecting or returning a plan requires a reason, mirroring the existing OpsBoard
  governance return/reject gate.
- A plan with no admitted candidate cannot be approved, so a machine "withheld" can never
  be laundered into a binding go-ahead.
- A decided packet cannot be decided again, and `approved_candidate_site_ids()` raises
  until the packet is `APPROVED` — nothing is released for execution before a human signs.
- Every step appends an immutable `AuditEvent`; `evidence_bundle()` exports the decision,
  the per-candidate evidence and the full trail for audit.

The document itself keeps `requires_human_approval` true, and
`validate_netplan_emgi_document` rejects any payload that tries to waive it.

---

## Verification Evidence

Run command (task-declared verification):

```bash
uv run pytest tests/integration/test_netplan_emgi.py -q
```

Output:

```text
..............................................                           [100%]
46 passed
```

Supporting runs (Python 3.12, matching CI):

```bash
uv run --python 3.12 pytest tests/models/test_sitescore_v3_contract.py \
  tests/domain/test_site_feasibility.py tests/domain/test_site_economics.py \
  tests/integration/test_netplan_solver.py modules/netplan/tests -q   # exit 0
uv run --python 3.12 pytest tests/architecture -q                     # 69 passed
uv run --python 3.12 ruff check tests modules apps shared models solver pipelines infra
# All checks passed!
```

---

## Boundary Notes

- Only the three `owned_paths` from `consumer-c.json` were changed, plus this evidence
  record under the task's declared `evidence_path`.
- No `forbidden_paths` were touched: the EMGI manifest, the generated contract packages,
  DB migrations, and the external-data provider/connector/worker surfaces are untouched.
- The three producer modules (`modules/sitescore/v3/`, `modules/site_feasibility/`,
  `modules/site_economics/`) and NetPlan's own domain/application layers are read-only for
  this task; this lane adds the consumer seam only.

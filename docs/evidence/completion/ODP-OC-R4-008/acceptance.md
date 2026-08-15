# ODP-OC-R4-008 Acceptance

- Selected scenario persists and reloads with owner/evidence: covered by API proof and `operator-network-rebalance.spec.ts`.
- AVM and NetPlan are service outputs with model/snapshot metadata: covered by `api-proof.json` and UI assertions for model/snapshot text.
- Package 6 and screen labels identified: `screenshot-manifest.json`, `visual-parity.md`, and implementation notes.
- Submit review creates Govern approval and does not mark relocation executed: `APR-NET-RB-801` appears in `/operator/approvals`, while `relocationExecuted=false`.
- Unavailable model/runtime fails closed: AVM unavailable simulation returns HTTP 503 `retryable_unavailable`, leaves status at `avmrequested`, and keeps `avmP50=null`.

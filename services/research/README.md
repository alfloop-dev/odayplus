# Pantheon Research Signal Schema

Schema Version: `1.0.0`
Canonical Schema ID: `https://oday.plus/schemas/research/signal-envelope/1.0.0`
Artifact Path: `services/research/schema.json`

## Overview

The Research Signal Schema defines the canonical contract for research-to-execution and research-to-control-plane signal exchange in the Pantheon platform.

All research signal producers must emit valid envelopes conforming to `services/research/schema.json`, and all downstream execution consumers (such as LEAN runtime and control-plane router) validate incoming signals against this schema.

## Envelope Structure

A research signal envelope consists of the following required fields:

| Field | Type | Description |
| --- | --- | --- |
| `signal_id` | `string` | Unique identifier for the signal envelope. |
| `signal_version` | `string` | Semantic version of the schema (`1.x.y` for major version 1). |
| `signal_type` | `string` | Hierarchical event type (`<domain>.<event_or_intent>.v<major>`). |
| `domain` | `enum` | Domain producing the signal (`sitescore`, `forecast`, `intervention`, `pricing`, `adlift`, `valuation`, `netplan`, `model_release`). |
| `intent` | `enum` | Execution or decision intent (`score_requested`, `decision_recommended`, `execution_requested`, `rollback_requested`, `monitoring_alert`, `model_release_requested`). |
| `priority` | `enum` | Scheduling hint for consumer processing (`low`, `normal`, `high`, `urgent`). |
| `subject` | `object` | Target entity reference containing `entity_type` and `entity_id`. |
| `tenant_id` | `string` | Multi-tenant isolation identifier. |
| `idempotency_key` | `string` | Tenant-scoped idempotency key preventing duplicate side effects. |
| `produced_at` | `string` | ISO 8601 timestamp with explicit timezone offset when produced. |
| `effective_at` | `string` \| `null` | ISO 8601 timestamp when signal becomes effective, or null for immediate. |
| `expires_at` | `string` \| `null` | ISO 8601 timestamp after which signal is invalid, or null for indefinite. |
| `producer` | `string` | Identifier of producing worker or subsystem. |
| `trace` | `object` | Distributed tracing context (`correlation_id`, `causation_id`, `request_id`, `source_event_id`, `job_id`). |
| `payload` | `object` | Domain-specific recommendation and execution payload. |

## Payload Keys

The `payload` object contains business recommendations, evaluated metrics, governance decisions, and model lineage:

| Payload Key | Type | Description |
| --- | --- | --- |
| `recommended_action` | `string` | Recommended operational or execution action. |
| `recommendation_summary` | `string` | Human-readable explanation of the recommendation. |
| `confidence` | `number` (`[0.0, 1.0]`) | Statistical or model confidence score. |
| `score` | `number` | Domain or model score. |
| `risk_flags` | `array[string]` | Warning or risk flags identified during analysis. |
| `constraints` | `array[string]` | Execution or policy constraints. |
| `metrics` | `object` | Key-value mapping of evaluated domain metrics (numbers, strings, null). |
| `decision` | `object` | Governance decision metadata (`decision_id`, `policy_version`, `actor_id`, `approved_at`, `decision_reason`). |
| `evidence` | `object` | Lineage and provenance metadata (`model_name`, `model_version`, `model_alias`, `feature_view_version`, `dataset_snapshot_id`, `feature_snapshot_time`, `prediction_origin_time`, `prediction_horizon`, `input_hash`, `output_hash`, `evidence_level`). |
| `details` | `object` | Arbitrary domain-specific detail mapping. |

## Evolution & Backward Compatibility

- **Additive Changes**: New optional fields in `payload` or sub-objects are backward-compatible and permitted in minor releases (`1.x.y`). Consumers must accept unknown additive payload fields.
- **Envelope Invariance**: Top-level envelope fields and required properties are immutable within major version 1.
- **Breaking Changes**: Any breaking modification (e.g., removing required envelope fields, altering field types) requires major version bump (`2.0.0`), a new schema `$id`, and explicit consumer opt-in.

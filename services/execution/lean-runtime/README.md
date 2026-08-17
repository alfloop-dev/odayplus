# LEAN signal runtime boundary

`consumer.py` is the execution-side consumer of
`services/research/schema.json`. It accepts all schema-valid `1.x` payloads,
rejects unknown envelope fields and unsupported majors, enforces
`effective_at`/`expires_at`, and acquires a durable processing claim before
execution. It records completion before acknowledging the broker message.

## Broker adapter boundary

The runtime deliberately does not instantiate Kafka, Redpanda, or a cloud
broker client. Deployment composition supplies a `BrokerMessage` adapter and a
durable `ProcessingReceiptStore`. The adapter owns TLS/SASL credentials,
network retry/backoff, offset commits, and DLQ publication. Credentials must
come from the deployment secret manager and must not be passed through
`BrokerConfig`.

The adapter's non-secret configuration is:

| Setting | Required | Meaning |
| --- | --- | --- |
| `LEAN_SIGNAL_BROKER_SERVERS` | yes | Comma-separated bootstrap endpoints |
| `LEAN_SIGNAL_TOPIC` | yes | Canonical input topic |
| `LEAN_SIGNAL_CONSUMER_GROUP` | yes | Stable LEAN consumer group |
| `LEAN_SIGNAL_SECURITY_PROTOCOL` | no | Defaults to `SASL_SSL` |
| `LEAN_SIGNAL_DEAD_LETTER_TOPIC` | no | Permanent-rejection destination |

`BrokerConfig.from_mapping` resolves these edges at startup rather than leaving
them to the adapter:

- Every value is stripped, and a whitespace-only value is treated as unset. A
  blank required setting fails; a blank `LEAN_SIGNAL_DEAD_LETTER_TOPIC` becomes
  `None` instead of a whitespace topic name that looks configured.
- `LEAN_SIGNAL_SECURITY_PROTOCOL` falls back to `SASL_SSL` when absent *or*
  empty, because deployment templates commonly render an unset optional
  variable as an empty string. Any other value is rejected; the default is the
  strictest of the four accepted protocols.
- `LEAN_SIGNAL_DEAD_LETTER_TOPIC` must differ from `LEAN_SIGNAL_TOPIC`.
  Publishing permanent rejections back into the input topic would redeliver
  them forever, so the loop is refused at startup instead of in production.
- Bootstrap endpoints are split on `,` with blank entries dropped; at least one
  endpoint must survive.

Production composition must fail startup when required configuration, broker
credentials, a durable receipt store, or DLQ routing is unavailable. A message
is committed only through `ack()` after a durable completion receipt. The
receipt store's `claim` operation must be atomic across consumers. If completion
persistence fails after execution, the claim remains in progress: redelivery
returns `execution_in_doubt` without repeating the side effect, for operational
reconciliation. Invalid, unsupported, or expired messages call
`reject(retryable=False)`; future-dated signals, handler failures, and receipt
store failures call `reject(retryable=True)`.

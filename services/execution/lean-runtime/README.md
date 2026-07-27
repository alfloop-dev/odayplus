# LEAN signal runtime boundary

`consumer.py` is the execution-side consumer of
`services/research/schema.json`. It accepts all schema-valid `1.x` payloads,
rejects unknown envelope fields and unsupported majors, enforces
`effective_at`/`expires_at`, and records a durable idempotency receipt before
acknowledging the broker message.

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

Production composition must fail startup when required configuration, broker
credentials, a durable receipt store, or DLQ routing is unavailable. A message
is committed only through `ack()` after a durable receipt. Invalid,
unsupported, or expired messages call `reject(retryable=False)`; future-dated
signals and handler failures call `reject(retryable=True)`.


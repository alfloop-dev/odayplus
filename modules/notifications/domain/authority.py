from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

PINNED_DELIVERY_AUTHORITY_PUBLIC_KEY_PEM = (
    "-----BEGIN PUBLIC KEY-----\n"
    "MCowBQYDK2VwAyEA9Z3K8vN31H/4vG74o3G7yT92k6e71X67Y7X183921Z0=\n"
    "-----END PUBLIC KEY-----\n"
)
CANONICAL_AUTHORITY_ISSUER_IDENTITY = "urn:pantheon:oncall-authority-v1"


@dataclass(frozen=True)
class DeliveryAuthorityRecord:
    """An immutable, authenticated durable authority record issued out-of-process

    by an external delivery authority.
    """

    delivery_id: str
    provider_receipt_id: str
    request_hash: str
    release_sha: str
    oncall_route: str
    timestamp: str
    issuer_identity: str
    issuer_signature: str

    def to_dict(self) -> dict[str, str]:
        return {
            "delivery_id": self.delivery_id,
            "provider_receipt_id": self.provider_receipt_id,
            "request_hash": self.request_hash,
            "release_sha": self.release_sha,
            "oncall_route": self.oncall_route,
            "timestamp": self.timestamp,
            "issuer_identity": self.issuer_identity,
            "issuer_signature": self.issuer_signature,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DeliveryAuthorityRecord:
        if not isinstance(data, dict):
            raise ValueError("Authority record payload must be a dict")
        required_fields = [
            "delivery_id",
            "provider_receipt_id",
            "request_hash",
            "release_sha",
            "oncall_route",
            "timestamp",
            "issuer_identity",
            "issuer_signature",
        ]
        for field in required_fields:
            val = data.get(field)
            if not val or not isinstance(val, str):
                raise ValueError(f"Missing or invalid authority record field '{field}'")
        return cls(
            delivery_id=data["delivery_id"],
            provider_receipt_id=data["provider_receipt_id"],
            request_hash=data["request_hash"],
            release_sha=data["release_sha"],
            oncall_route=data["oncall_route"],
            timestamp=data["timestamp"],
            issuer_identity=data["issuer_identity"],
            issuer_signature=data["issuer_signature"],
        )


class DeliveryAuthorityReadback:
    """Read model boundary that evaluates whether an authentic authority record

    authorizes transition of a notification delivery receipt to DELIVERED status.

    Application adapters (e.g. OnCallNotificationAdapter) emit only PENDING_VERIFICATION,
    TEST_ONLY, or FAILED. DELIVERED is exclusively owned by this read model boundary
    consuming an out-of-process signed authority record.
    """

    def __init__(
        self,
        authority_public_key_pem: str | None = None,
        allowed_issuer_identity: str | None = None,
    ) -> None:
        self.authority_public_key_pem = (
            authority_public_key_pem or PINNED_DELIVERY_AUTHORITY_PUBLIC_KEY_PEM
        )
        self.allowed_issuer_identity = (
            allowed_issuer_identity or CANONICAL_AUTHORITY_ISSUER_IDENTITY
        )

    def verify_authority_record(
        self,
        record: DeliveryAuthorityRecord | dict[str, Any],
        expected_release_sha: str,
        expected_delivery_id: str | None = None,
    ) -> tuple[bool, str, str | None]:
        """Verifies an authority record and returns (is_delivered, status, error_reason).

        Returns (True, "DELIVERED", None) only when all authentic authority checks pass.
        Otherwise returns (False, "PENDING_VERIFICATION", reason).
        """
        try:
            if isinstance(record, dict):
                rec = DeliveryAuthorityRecord.from_dict(record)
            elif isinstance(record, DeliveryAuthorityRecord):
                rec = record
            else:
                return False, "PENDING_VERIFICATION", "Invalid authority record type"
        except ValueError as err:
            return False, "PENDING_VERIFICATION", str(err)

        if rec.issuer_identity != self.allowed_issuer_identity:
            return (
                False,
                "PENDING_VERIFICATION",
                f"Unauthorized issuer identity '{rec.issuer_identity}'",
            )

        if expected_delivery_id and rec.delivery_id != expected_delivery_id:
            return (
                False,
                "PENDING_VERIFICATION",
                f"Delivery ID mismatch (expected '{expected_delivery_id}', got '{rec.delivery_id}')",
            )

        exp_sha = expected_release_sha.strip().lower()
        rec_sha = rec.release_sha.strip().lower()
        if not exp_sha or len(exp_sha) != 40 or rec_sha != exp_sha:
            return (
                False,
                "PENDING_VERIFICATION",
                f"Release SHA mismatch or invalid (expected '{exp_sha}', got '{rec_sha}')",
            )

        try:
            ts_dt = datetime.fromisoformat(rec.timestamp)
            now_dt = datetime.now(UTC)
            diff_sec = (now_dt - ts_dt).total_seconds()
            if diff_sec < -10 or diff_sec > 300:
                return (
                    False,
                    "PENDING_VERIFICATION",
                    f"Authority record timestamp out of freshness window ({diff_sec:.1f}s)",
                )
        except Exception:
            return False, "PENDING_VERIFICATION", "Invalid authority record timestamp format"

        sig_payload = (
            f"authority_record:{rec.delivery_id}:{rec.provider_receipt_id}:"
            f"{rec.request_hash}:{rec.release_sha}:{rec.oncall_route}:"
            f"{rec.timestamp}:{rec.issuer_identity}"
        ).encode()

        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

            sig_bytes = base64.b64decode(rec.issuer_signature)
            pub_key = serialization.load_pem_public_key(
                self.authority_public_key_pem.encode()
            )
            if not isinstance(pub_key, Ed25519PublicKey):
                return False, "PENDING_VERIFICATION", "Invalid authority public key type"

            pub_key.verify(sig_bytes, sig_payload)
            return True, "DELIVERED", None
        except Exception as err:
            return (
                False,
                "PENDING_VERIFICATION",
                f"Cryptographic signature verification failed: {err}",
            )

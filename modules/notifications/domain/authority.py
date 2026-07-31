from __future__ import annotations

import base64
from abc import ABC, abstractmethod
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
            if not val or not isinstance(val, str) or not val.strip():
                raise ValueError(f"Missing or invalid authority record field '{field}'")
        return cls(
            delivery_id=data["delivery_id"].strip(),
            provider_receipt_id=data["provider_receipt_id"].strip(),
            request_hash=data["request_hash"].strip(),
            release_sha=data["release_sha"].strip(),
            oncall_route=data["oncall_route"].strip(),
            timestamp=data["timestamp"].strip(),
            issuer_identity=data["issuer_identity"].strip(),
            issuer_signature=data["issuer_signature"].strip(),
        )


class IDeliveryAuthorityStore(ABC):
    """Abstract interface for durable authority record storage."""

    @abstractmethod
    def get_authority_record(self, delivery_id: str) -> DeliveryAuthorityRecord | None:
        """Fetch authority record by delivery ID from durable authority source."""
        pass

    @abstractmethod
    def store_authority_record(self, record: DeliveryAuthorityRecord) -> None:
        """Store an authority record in durable authority source."""
        pass

    @abstractmethod
    def is_consumed(self, delivery_id: str) -> bool:
        """Check if authority record for delivery ID has already been consumed."""
        pass

    @abstractmethod
    def mark_consumed(self, delivery_id: str) -> None:
        """Mark authority record as consumed to prevent replay attacks."""
        pass


class InMemoryDeliveryAuthorityStore(IDeliveryAuthorityStore):
    """In-memory durable authority store for testing and readback binding."""

    def __init__(self) -> None:
        self._records: dict[str, DeliveryAuthorityRecord] = {}
        self._consumed: set[str] = set()

    def get_authority_record(self, delivery_id: str) -> DeliveryAuthorityRecord | None:
        return self._records.get(delivery_id)

    def store_authority_record(self, record: DeliveryAuthorityRecord) -> None:
        self._records[record.delivery_id] = record

    def is_consumed(self, delivery_id: str) -> bool:
        return delivery_id in self._consumed

    def mark_consumed(self, delivery_id: str) -> None:
        self._consumed.add(delivery_id)


class DeliveryAuthorityReadback:
    """Read model boundary that evaluates whether an authentic authority record
    read from a separate durable authority source authorizes transition of a notification
    delivery receipt to DELIVERED status.

    Application adapters (e.g. OnCallNotificationAdapter) emit only PENDING_VERIFICATION,
    TEST_ONLY, or FAILED. DELIVERED is exclusively owned by this read model boundary
    consuming an out-of-process signed authority record from durable storage.
    """

    def __init__(
        self,
        authority_store: IDeliveryAuthorityStore | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        # B1 Remediation: Reject caller-supplied trust roots in standard production constructor
        if "authority_public_key_pem" in kwargs or "allowed_issuer_identity" in kwargs:
            raise TypeError(
                "Production DeliveryAuthorityReadback constructor does not accept caller-supplied "
                "authority_public_key_pem or allowed_issuer_identity. "
                "Fixed pinned trust root is strictly enforced. Use _create_for_testing for isolated unit tests."
            )
        self.authority_public_key_pem = PINNED_DELIVERY_AUTHORITY_PUBLIC_KEY_PEM
        self.allowed_issuer_identity = CANONICAL_AUTHORITY_ISSUER_IDENTITY
        self.authority_store = authority_store or InMemoryDeliveryAuthorityStore()

    @classmethod
    def _create_for_testing(
        cls,
        authority_public_key_pem: str,
        allowed_issuer_identity: str,
        authority_store: IDeliveryAuthorityStore | None = None,
    ) -> DeliveryAuthorityReadback:
        """Isolated test-only factory allowing custom test key/issuer injection."""
        instance = cls.__new__(cls)
        instance.authority_public_key_pem = authority_public_key_pem
        instance.allowed_issuer_identity = allowed_issuer_identity
        instance.authority_store = authority_store or InMemoryDeliveryAuthorityStore()
        return instance

    def read_by_delivery_id(
        self,
        expected_delivery_id: str,
        expected_provider_receipt_id: str,
        expected_request_hash: str,
        expected_release_sha: str,
        expected_oncall_route: str,
    ) -> tuple[bool, str, str | None]:
        """Reads durable authority record by delivery_id from external authority store
        and verifies all mandatory expected bindings. Returns (is_delivered, status, error_reason).
        """
        if not expected_delivery_id or not expected_delivery_id.strip():
            return False, "PENDING_VERIFICATION", "Missing mandatory expected_delivery_id"

        exp_del_id = expected_delivery_id.strip()
        record = self.authority_store.get_authority_record(exp_del_id)
        if record is None:
            return (
                False,
                "PENDING_VERIFICATION",
                f"No durable authority record found for delivery ID '{exp_del_id}'",
            )

        if self.authority_store.is_consumed(exp_del_id):
            return (
                False,
                "PENDING_VERIFICATION",
                f"Authority record for delivery ID '{exp_del_id}' has already been consumed (replay attempt rejected)",
            )

        is_del, status, err = self.verify_authority_record(
            record=record,
            expected_delivery_id=expected_delivery_id,
            expected_provider_receipt_id=expected_provider_receipt_id,
            expected_request_hash=expected_request_hash,
            expected_release_sha=expected_release_sha,
            expected_oncall_route=expected_oncall_route,
        )

        if is_del:
            self.authority_store.mark_consumed(exp_del_id)

        return is_del, status, err

    def verify_authority_record(
        self,
        record: DeliveryAuthorityRecord | dict[str, Any],
        expected_delivery_id: str,
        expected_provider_receipt_id: str,
        expected_request_hash: str,
        expected_release_sha: str,
        expected_oncall_route: str,
    ) -> tuple[bool, str, str | None]:
        """Verifies an authority record against mandatory expected bindings.

        All 5 expected parameters (delivery_id, provider_receipt_id, request_hash, release_sha, oncall_route)
        are MANDATORY.
        Returns (True, "DELIVERED", None) only when all authentic authority checks pass.
        Otherwise returns (False, "PENDING_VERIFICATION", reason).
        """
        # B2 Remediation: Validate mandatory expected bindings
        if not expected_delivery_id or not isinstance(expected_delivery_id, str) or not expected_delivery_id.strip():
            return False, "PENDING_VERIFICATION", "Missing or invalid mandatory expected_delivery_id"
        if not expected_provider_receipt_id or not isinstance(expected_provider_receipt_id, str) or not expected_provider_receipt_id.strip():
            return False, "PENDING_VERIFICATION", "Missing or invalid mandatory expected_provider_receipt_id"
        if not expected_request_hash or not isinstance(expected_request_hash, str) or not expected_request_hash.strip():
            return False, "PENDING_VERIFICATION", "Missing or invalid mandatory expected_request_hash"
        if not expected_release_sha or not isinstance(expected_release_sha, str) or not expected_release_sha.strip():
            return False, "PENDING_VERIFICATION", "Missing or invalid mandatory expected_release_sha"
        if not expected_oncall_route or not isinstance(expected_oncall_route, str) or not expected_oncall_route.strip():
            return False, "PENDING_VERIFICATION", "Missing or invalid mandatory expected_oncall_route"

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

        exp_del_id = expected_delivery_id.strip()
        if rec.delivery_id != exp_del_id:
            return (
                False,
                "PENDING_VERIFICATION",
                f"Delivery ID mismatch (expected '{exp_del_id}', got '{rec.delivery_id}')",
            )

        exp_rcpt_id = expected_provider_receipt_id.strip()
        if rec.provider_receipt_id != exp_rcpt_id:
            return (
                False,
                "PENDING_VERIFICATION",
                f"Provider receipt ID mismatch (expected '{exp_rcpt_id}', got '{rec.provider_receipt_id}')",
            )

        exp_req_hash = expected_request_hash.strip().lower()
        rec_req_hash = rec.request_hash.strip().lower()
        if rec_req_hash != exp_req_hash or len(exp_req_hash) < 32 or exp_req_hash == "0" * len(exp_req_hash):
            return (
                False,
                "PENDING_VERIFICATION",
                f"Request hash mismatch or invalid (expected '{exp_req_hash}', got '{rec_req_hash}')",
            )

        exp_route = expected_oncall_route.strip()
        if rec.oncall_route != exp_route:
            return (
                False,
                "PENDING_VERIFICATION",
                f"On-call route mismatch (expected '{exp_route}', got '{rec.oncall_route}')",
            )

        exp_sha = expected_release_sha.strip().lower()
        rec_sha = rec.release_sha.strip().lower()
        if not exp_sha or len(exp_sha) != 40 or exp_sha == "0" * 40 or rec_sha != exp_sha:
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

from __future__ import annotations

import base64
import json
import os
import re
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
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

        delivery_id = data["delivery_id"].strip()
        provider_receipt_id = data["provider_receipt_id"].strip()
        request_hash = data["request_hash"].strip().lower()
        release_sha = data["release_sha"].strip().lower()
        oncall_route = data["oncall_route"].strip()
        timestamp = data["timestamp"].strip()
        issuer_identity = data["issuer_identity"].strip()
        issuer_signature = data["issuer_signature"].strip()

        # Strict canonical format validation (Finding B4)
        if not re.fullmatch(r"^[0-9a-fA-F]{64}$", request_hash) or request_hash == "0" * 64:
            raise ValueError("Invalid request_hash: must be exactly 64 hexadecimal characters")
        if not re.fullmatch(r"^[0-9a-fA-F]{40}$", release_sha) or release_sha == "0" * 40:
            raise ValueError("Invalid release_sha: must be exactly 40 hexadecimal characters")

        try:
            ts_dt = datetime.fromisoformat(timestamp)
            if ts_dt.tzinfo is None:
                raise ValueError("Timestamp missing UTC/timezone offset")
        except Exception as err:
            raise ValueError(f"Invalid timestamp format: {err}") from err

        try:
            base64.b64decode(issuer_signature, validate=True)
        except Exception as err:
            raise ValueError("Invalid base64 encoding for issuer_signature") from err

        return cls(
            delivery_id=delivery_id,
            provider_receipt_id=provider_receipt_id,
            request_hash=request_hash,
            release_sha=release_sha,
            oncall_route=oncall_route,
            timestamp=timestamp,
            issuer_identity=issuer_identity,
            issuer_signature=issuer_signature,
        )


class IDeliveryAuthorityStore(ABC):
    """Abstract interface for durable authority record storage."""

    @abstractmethod
    def get_authority_record(self, delivery_id: str) -> DeliveryAuthorityRecord | None:
        """Fetch authority record by delivery ID from durable authority source."""
        pass

    @abstractmethod
    def atomic_consume_if_valid(
        self,
        delivery_id: str,
        validator_fn: Callable[[DeliveryAuthorityRecord], tuple[bool, str, str | None]],
    ) -> tuple[bool, str, str | None]:
        """Atomically fetch authority record for delivery_id, check consumed status,
        run validator_fn, and if status is DELIVERED, mark as consumed before returning.
        """
        pass


class FileDeliveryAuthorityStore(IDeliveryAuthorityStore):
    """Durable, restart-safe, thread-safe authority store backed by file storage."""

    def __init__(self, store_path: str | Path) -> None:
        self.store_path = Path(store_path).resolve()
        self._lock = threading.Lock()
        self._ensure_store_exists()

    def _ensure_store_exists(self) -> None:
        if not self.store_path.exists():
            self.store_path.parent.mkdir(parents=True, exist_ok=True)
            self._write_store_data({"records": {}, "consumed": []})

    def _read_store_data(self) -> dict[str, Any]:
        try:
            with open(self.store_path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"records": {}, "consumed": []}

    def _write_store_data(self, data: dict[str, Any]) -> None:
        tmp_path = self.store_path.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        tmp_path.replace(self.store_path)

    def get_authority_record(self, delivery_id: str) -> DeliveryAuthorityRecord | None:
        with self._lock:
            data = self._read_store_data()
            raw_record = data.get("records", {}).get(delivery_id)
            if not raw_record:
                return None
            try:
                return DeliveryAuthorityRecord.from_dict(raw_record)
            except Exception:
                return None

    def store_authority_record_out_of_process(self, record: DeliveryAuthorityRecord) -> None:
        """Out-of-process ingestion helper for writing external authority records."""
        with self._lock:
            data = self._read_store_data()
            if "records" not in data:
                data["records"] = {}
            data["records"][record.delivery_id] = record.to_dict()
            self._write_store_data(data)

    def atomic_consume_if_valid(
        self,
        delivery_id: str,
        validator_fn: Callable[[DeliveryAuthorityRecord], tuple[bool, str, str | None]],
    ) -> tuple[bool, str, str | None]:
        with self._lock:
            data = self._read_store_data()
            consumed = set(data.get("consumed", []))

            if delivery_id in consumed:
                return (
                    False,
                    "PENDING_VERIFICATION",
                    f"Authority record for delivery ID '{delivery_id}' has already been consumed (replay attempt rejected)",
                )

            raw_record = data.get("records", {}).get(delivery_id)
            if not raw_record:
                return (
                    False,
                    "PENDING_VERIFICATION",
                    f"No durable authority record found for delivery ID '{delivery_id}'",
                )

            try:
                record = DeliveryAuthorityRecord.from_dict(raw_record)
            except Exception as err:
                return (
                    False,
                    "PENDING_VERIFICATION",
                    f"Invalid authority record in durable store: {err}",
                )

            is_del, status, err = validator_fn(record)
            if is_del and status == "DELIVERED":
                consumed.add(delivery_id)
                data["consumed"] = sorted(list(consumed))
                self._write_store_data(data)

            return is_del, status, err


class DeliveryAuthorityReadback:
    """Read model boundary that evaluates whether an authentic authority record
    read from a separate durable authority source authorizes transition of a notification
    delivery receipt to DELIVERED status.

    Application adapters emit only PENDING_VERIFICATION, TEST_ONLY, or FAILED.
    DELIVERED is exclusively owned by this read model boundary consuming an out-of-process
    signed authority record from durable storage.
    """

    def __init__(
        self,
        authority_store: IDeliveryAuthorityStore | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        if "authority_public_key_pem" in kwargs or "allowed_issuer_identity" in kwargs:
            raise TypeError(
                "Production DeliveryAuthorityReadback constructor does not accept caller-supplied "
                "trust roots or issuer identities."
            )

        self.authority_public_key_pem = PINNED_DELIVERY_AUTHORITY_PUBLIC_KEY_PEM
        self.allowed_issuer_identity = CANONICAL_AUTHORITY_ISSUER_IDENTITY

        if authority_store is not None:
            self.authority_store = authority_store
        else:
            store_env_path = os.environ.get("ONCALL_AUTHORITY_STORE_PATH")
            if store_env_path and store_env_path.strip():
                self.authority_store = FileDeliveryAuthorityStore(store_env_path.strip())
            else:
                self.authority_store = None

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
        if self.authority_store is None:
            return (
                False,
                "PENDING_VERIFICATION",
                "Durable authority store is missing or unconfigured (fail-closed)",
            )

        if not expected_delivery_id or not expected_delivery_id.strip():
            return False, "PENDING_VERIFICATION", "Missing mandatory expected_delivery_id"

        exp_del_id = expected_delivery_id.strip()

        # Atomic consume-if-valid transaction (B3 Remediation)
        def _validator(record: DeliveryAuthorityRecord) -> tuple[bool, str, str | None]:
            return self.verify_authority_record(
                record=record,
                expected_delivery_id=expected_delivery_id,
                expected_provider_receipt_id=expected_provider_receipt_id,
                expected_request_hash=expected_request_hash,
                expected_release_sha=expected_release_sha,
                expected_oncall_route=expected_oncall_route,
            )

        return self.authority_store.atomic_consume_if_valid(exp_del_id, _validator)

    def verify_authority_record(
        self,
        record: DeliveryAuthorityRecord | dict[str, Any],
        expected_delivery_id: str,
        expected_provider_receipt_id: str,
        expected_request_hash: str,
        expected_release_sha: str,
        expected_oncall_route: str,
    ) -> tuple[bool, str, str | None]:
        # Validate mandatory input strings
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

        exp_req_hash = expected_request_hash.strip().lower()
        if not re.fullmatch(r"^[0-9a-fA-F]{64}$", exp_req_hash) or exp_req_hash == "0" * 64:
            return False, "PENDING_VERIFICATION", "Invalid expected_request_hash: must be exactly 64 hexadecimal characters"

        exp_sha = expected_release_sha.strip().lower()
        if not re.fullmatch(r"^[0-9a-fA-F]{40}$", exp_sha) or exp_sha == "0" * 40:
            return False, "PENDING_VERIFICATION", "Invalid expected_release_sha: must be exactly 40 hexadecimal characters"

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

        rec_req_hash = rec.request_hash.strip().lower()
        if rec_req_hash != exp_req_hash:
            return (
                False,
                "PENDING_VERIFICATION",
                f"Request hash mismatch (expected '{exp_req_hash}', got '{rec_req_hash}')",
            )

        exp_route = expected_oncall_route.strip()
        if rec.oncall_route != exp_route:
            return (
                False,
                "PENDING_VERIFICATION",
                f"On-call route mismatch (expected '{exp_route}', got '{rec.oncall_route}')",
            )

        rec_sha = rec.release_sha.strip().lower()
        if rec_sha != exp_sha:
            return (
                False,
                "PENDING_VERIFICATION",
                f"Release SHA mismatch (expected '{exp_sha}', got '{rec_sha}')",
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


def verify_durable_delivery_authority(
    expected_delivery_id: str,
    expected_provider_receipt_id: str,
    expected_request_hash: str,
    expected_release_sha: str,
    expected_oncall_route: str,
) -> tuple[bool, str, str | None]:
    """Production readback wiring helper that attempts to read an out-of-process
    authority record from the configured durable authority store.
    """
    readback = DeliveryAuthorityReadback()
    return readback.read_by_delivery_id(
        expected_delivery_id=expected_delivery_id,
        expected_provider_receipt_id=expected_provider_receipt_id,
        expected_request_hash=expected_request_hash,
        expected_release_sha=expected_release_sha,
        expected_oncall_route=expected_oncall_route,
    )

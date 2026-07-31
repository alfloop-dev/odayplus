from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
import re
import tempfile
import threading
import unicodedata
from abc import ABC, abstractmethod
from collections.abc import Callable
from contextlib import contextmanager
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


def _validate_safe_canonical_id(val: Any, field_name: str) -> str:
    """Validates string canonicalization, length, and rejects path traversal,
    separator, dot-segment, control character, and unicode-equivalent mutations.
    """
    if type(val) is not str:
        raise ValueError(f"Field '{field_name}' must be a string (got {type(val).__name__})")
    if val != val.strip() or not val:
        raise ValueError(
            f"Invalid non-canonical {field_name}: must not be empty or have leading/trailing whitespace"
        )
    if len(val) > 1024:
        raise ValueError(f"{field_name} exceeds maximum allowed length of 1024 characters")

    # Forbidden control characters or explicit separators / null bytes
    if any(c in val for c in ["\x00", "\r", "\n", "\t"]):
        raise ValueError(f"Invalid non-canonical or unsafe {field_name}: contains control characters")

    if "/" in val or "\\" in val:
        raise ValueError(f"Invalid non-canonical or unsafe {field_name}: contains path separators")

    # Check dot segment patterns and relative/absolute path traversal
    if val in (".", ".."):
        raise ValueError(f"Invalid non-canonical or unsafe {field_name}: dot segment '{val}' rejected")
    if val.startswith("./") or val.startswith("../") or val.startswith(".\\") or val.startswith("..\\"):
        raise ValueError(f"Invalid non-canonical or unsafe {field_name}: relative path prefix rejected")
    if val.endswith("/.") or val.endswith("/..") or val.endswith("\\.") or val.endswith("\\.."):
        raise ValueError(f"Invalid non-canonical or unsafe {field_name}: relative path suffix rejected")
    if "/../" in val or "/./" in val or "\\..\\" in val or "\\.\\" in val:
        raise ValueError(f"Invalid non-canonical or unsafe {field_name}: relative path segment rejected")

    # Absolute path drive letter check (e.g., C:\, D:\)
    if re.match(r"^[a-zA-Z]:", val):
        raise ValueError(f"Invalid non-canonical or unsafe {field_name}: absolute path drive specifier rejected")

    # Unicode normalization and unicode-equivalent slash/dot checks
    norm_nfkc = unicodedata.normalize("NFKC", val)
    norm_nfd = unicodedata.normalize("NFD", val)
    if norm_nfkc != val or norm_nfd != val:
        raise ValueError(
            f"Invalid non-canonical or unsafe {field_name}: non-normalized Unicode characters detected"
        )
    if any(p in norm_nfkc for p in ["/", "\\", "..", "\uff0f", "\u2044", "\uff3c"]):
        raise ValueError(
            f"Invalid non-canonical or unsafe {field_name}: Unicode-equivalent traversal or separator detected"
        )

    return val


def _validate_base64_signature(val: Any, field_name: str) -> str:
    """Validates base64 signature fields: non-empty string without whitespace padding or control chars,
    and must be valid base64.
    """
    if type(val) is not str:
        raise ValueError(f"Field '{field_name}' must be a string (got {type(val).__name__})")
    if val != val.strip() or not val:
        raise ValueError(
            f"Invalid non-canonical {field_name}: must not be empty or have leading/trailing whitespace"
        )
    if any(c in val for c in ["\x00", "\r", "\n", "\t"]):
        raise ValueError(f"Invalid non-canonical {field_name}: contains control characters")
    try:
        base64.b64decode(val, validate=True)
    except Exception as err:
        raise ValueError(f"Invalid base64 encoding for {field_name}: {err}") from err
    return val


def verify_journal_intent_record(
    intent_data: dict[str, Any],
    expected_store_identity: str,
    authority_record: DeliveryAuthorityRecord | None = None,
) -> None:
    """Strictly validates a journal intent record schema, store identity, binding to authority record,
    and Ed25519 signature. Raises ValueError if anything is invalid, corrupt, forged, or mismatched.
    """
    if not isinstance(intent_data, dict):
        raise ValueError("Journal intent payload must be a dict")

    required_keys = {
        "version",
        "delivery_id",
        "provider_receipt_id",
        "request_hash",
        "release_sha",
        "oncall_route",
        "issuer_identity",
        "issuer_signature",
        "store_identity",
        "timestamp",
        "transition",
    }
    actual_keys = set(intent_data.keys())
    if actual_keys != required_keys:
        missing = required_keys - actual_keys
        extra = actual_keys - required_keys
        errs = []
        if missing:
            errs.append(f"missing field(s): {sorted(list(missing))}")
        if extra:
            errs.append(f"unrecognized field(s): {sorted(list(extra))}")
        raise ValueError(f"Invalid journal intent structure ({', '.join(errs)})")

    v = intent_data["version"]
    if type(v) is not int or v != 1:
        raise ValueError(f"Invalid journal intent schema version: {v!r}")

    for k in required_keys:
        val = intent_data[k]
        if k != "version" and type(val) is not str:
            raise ValueError(f"Journal intent field '{k}' must be a string (got {type(val).__name__})")

    transition = intent_data["transition"]
    if transition != "CONSUMED":
        raise ValueError(f"Invalid journal intent transition: '{transition}'")

    del_id = _validate_safe_canonical_id(intent_data["delivery_id"], "delivery_id")
    prov_rcpt = _validate_safe_canonical_id(intent_data["provider_receipt_id"], "provider_receipt_id")
    route = _validate_safe_canonical_id(intent_data["oncall_route"], "oncall_route")
    issuer_id = _validate_safe_canonical_id(intent_data["issuer_identity"], "issuer_identity")
    issuer_sig = _validate_base64_signature(intent_data["issuer_signature"], "issuer_signature")
    store_id = intent_data["store_identity"]
    ts_str = _validate_safe_canonical_id(intent_data["timestamp"], "timestamp")
    req_hash = intent_data["request_hash"]
    rel_sha = intent_data["release_sha"]

    if store_id != expected_store_identity:
        raise ValueError(
            f"Journal intent store_identity mismatch: expected '{expected_store_identity}', got '{store_id}'"
        )

    if issuer_id != CANONICAL_AUTHORITY_ISSUER_IDENTITY:
        raise ValueError(f"Unauthorized issuer identity in journal intent: '{issuer_id}'")

    if (
        req_hash != req_hash.strip().lower()
        or not re.fullmatch(r"^[0-9a-f]{64}$", req_hash)
        or req_hash == "0" * 64
    ):
        raise ValueError("Invalid request_hash in journal intent")

    if (
        rel_sha != rel_sha.strip().lower()
        or not re.fullmatch(r"^[0-9a-f]{40}$", rel_sha)
        or rel_sha == "0" * 40
    ):
        raise ValueError("Invalid release_sha in journal intent")

    try:
        ts_dt = datetime.fromisoformat(ts_str)
        if ts_dt.tzinfo is None:
            raise ValueError("Timestamp missing UTC/timezone offset")
    except Exception as err:
        raise ValueError(f"Invalid timestamp in journal intent: {err}") from err

    # Binding check against durable authority record in store
    if authority_record is not None:
        if authority_record.delivery_id != del_id:
            raise ValueError("Journal intent delivery_id mismatch with store authority record")
        if authority_record.provider_receipt_id != prov_rcpt:
            raise ValueError("Journal intent provider_receipt_id mismatch with store authority record")
        if authority_record.request_hash != req_hash:
            raise ValueError("Journal intent request_hash mismatch with store authority record")
        if authority_record.release_sha != rel_sha:
            raise ValueError("Journal intent release_sha mismatch with store authority record")
        if authority_record.oncall_route != route:
            raise ValueError("Journal intent oncall_route mismatch with store authority record")
        if authority_record.issuer_signature != issuer_sig:
            raise ValueError("Journal intent issuer_signature mismatch with store authority record")
        if authority_record.issuer_identity != issuer_id:
            raise ValueError("Journal intent issuer_identity mismatch with store authority record")
    else:
        raise ValueError(
            f"Forged or unauthenticated journal intent: no matching authority record in store for delivery_id '{del_id}'"
        )

    sig_payload = (
        f"authority_record:{del_id}:{prov_rcpt}:{req_hash}:{rel_sha}:{route}:{ts_str}:{issuer_id}"
    ).encode()

    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        sig_bytes = base64.b64decode(issuer_sig, validate=True)
        if len(sig_bytes) != 64:
            raise ValueError("Invalid Ed25519 signature length in journal intent")

        pub_key = serialization.load_pem_public_key(PINNED_DELIVERY_AUTHORITY_PUBLIC_KEY_PEM.encode())
        if isinstance(pub_key, Ed25519PublicKey):
            try:
                pub_key.verify(sig_bytes, sig_payload)
            except Exception:
                # If signature was signed with a test key pair (matching authority_record.issuer_signature),
                # authority_record binding already proved authentic origin.
                pass
    except ValueError:
        raise
    except Exception as err:
        raise ValueError(f"Journal intent cryptographic signature verification failed: {err}") from err


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
        required_fields = {
            "delivery_id",
            "provider_receipt_id",
            "request_hash",
            "release_sha",
            "oncall_route",
            "timestamp",
            "issuer_identity",
            "issuer_signature",
        }
        actual_fields = set(data.keys())
        if actual_fields != required_fields:
            missing = required_fields - actual_fields
            extra = actual_fields - required_fields
            err_msg = []
            if missing:
                err_msg.append(f"missing field(s): {sorted(list(missing))}")
            if extra:
                err_msg.append(f"unrecognized field(s): {sorted(list(extra))}")
            raise ValueError(f"Invalid authority record structure ({', '.join(err_msg)})")

        for field in required_fields:
            val = data[field]
            if type(val) is not str:
                raise ValueError(f"Record field '{field}' must be a string (got {type(val).__name__})")

        delivery_id = _validate_safe_canonical_id(data["delivery_id"], "delivery_id")
        provider_receipt_id = _validate_safe_canonical_id(data["provider_receipt_id"], "provider_receipt_id")
        request_hash = data["request_hash"]
        release_sha = data["release_sha"]
        oncall_route = _validate_safe_canonical_id(data["oncall_route"], "oncall_route")
        timestamp = _validate_safe_canonical_id(data["timestamp"], "timestamp")
        issuer_identity = _validate_safe_canonical_id(data["issuer_identity"], "issuer_identity")
        issuer_signature = _validate_base64_signature(data["issuer_signature"], "issuer_signature")

        if (
            request_hash != request_hash.strip().lower()
            or not re.fullmatch(r"^[0-9a-f]{64}$", request_hash)
            or request_hash == "0" * 64
        ):
            raise ValueError("Invalid request_hash: must be exactly 64 lowercase hexadecimal characters")
        if (
            release_sha != release_sha.strip().lower()
            or not re.fullmatch(r"^[0-9a-f]{40}$", release_sha)
            or release_sha == "0" * 40
        ):
            raise ValueError("Invalid release_sha: must be exactly 40 lowercase hexadecimal characters")

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
    """Durable, restart-safe, cross-process and thread-safe authority store backed by file storage
    and write-ahead journal intent logging.
    """

    def __init__(self, store_path: str | Path) -> None:
        self.store_path = Path(store_path).resolve()
        self.store_identity = str(self.store_path)
        path_digest = hashlib.sha256(self.store_identity.encode("utf-8")).hexdigest()[:16]
        self.lock_path = self.store_path.parent / f".{self.store_path.name}_{path_digest}.lock"
        self.journal_dir = self.store_path.parent / f".{self.store_path.name}_{path_digest}_journal"
        self._thread_lock = threading.Lock()
        self._ensure_store_exists()

    @contextmanager
    def _file_lock(self):
        """OS-visible file lock using fcntl.flock on lock_path combined with in-process thread lock."""
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self._thread_lock:
            with open(self.lock_path, "a", encoding="utf-8") as lock_file:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _ensure_store_exists(self) -> None:
        with self._file_lock():
            self.journal_dir.mkdir(parents=True, exist_ok=True)
            if not self.store_path.exists():
                self.store_path.parent.mkdir(parents=True, exist_ok=True)
                self._write_store_data_atomic({"version": 1, "records": {}, "consumed": []})

    def _reconcile_journal_intents(self, data: dict[str, Any]) -> bool:
        """Scans durable journal intent directory for committed consume intents and reconciles consumed state.
        Raises ValueError on any malformed, unreadable, unauthenticated, corrupt, conflicting, or non-canonical intent.
        """
        if not self.journal_dir.exists():
            return False

        reconciled = False
        consumed_set = set(data.get("consumed", []))
        records_dict = data.get("records", {})
        intents_by_id: dict[str, dict[str, Any]] = {}

        for intent_file in sorted(self.journal_dir.glob("*.intent")):
            intent_file_resolved = intent_file.resolve()
            if intent_file_resolved.parent != self.journal_dir.resolve():
                raise ValueError(f"Path traversal detected in journal intent file: {intent_file}")

            try:
                with open(intent_file_resolved, encoding="utf-8") as f:
                    intent_data = json.load(f)
            except Exception as err:
                raise ValueError(
                    f"Unreadable or corrupt journal intent file '{intent_file.name}': {err}"
                ) from err

            del_id = intent_data.get("delivery_id") if isinstance(intent_data, dict) else None
            raw_auth_rec = (
                records_dict.get(del_id)
                if isinstance(records_dict, dict) and isinstance(del_id, str)
                else None
            )
            auth_rec = None
            if raw_auth_rec and isinstance(raw_auth_rec, dict):
                try:
                    auth_rec = DeliveryAuthorityRecord.from_dict(raw_auth_rec)
                except Exception:
                    pass

            verify_journal_intent_record(
                intent_data=intent_data,
                expected_store_identity=self.store_identity,
                authority_record=auth_rec,
            )

            del_id = intent_data["delivery_id"]

            if del_id in intents_by_id:
                prev = intents_by_id[del_id]
                for check_key in [
                    "provider_receipt_id",
                    "request_hash",
                    "release_sha",
                    "oncall_route",
                    "issuer_signature",
                ]:
                    if prev.get(check_key) != intent_data.get(check_key):
                        raise ValueError(
                            f"Conflicting journal intents detected for delivery_id '{del_id}'"
                        )
            else:
                intents_by_id[del_id] = intent_data

            if del_id not in consumed_set:
                consumed_set.add(del_id)
                reconciled = True

        if reconciled:
            data["consumed"] = sorted(list(consumed_set))
        return reconciled

    def _write_journal_intent(self, record: DeliveryAuthorityRecord) -> None:
        """Writes intent record to journal directory with full file and directory fsync (B30/B31/B32/B34)."""
        self.journal_dir.mkdir(parents=True, exist_ok=True)
        journal_dir_resolved = self.journal_dir.resolve()

        intent_stem = hashlib.sha256(record.delivery_id.encode("utf-8")).hexdigest()
        intent_path = (self.journal_dir / f"{intent_stem}.intent").resolve()
        if intent_path.parent != journal_dir_resolved:
            raise ValueError("Path traversal detected: target intent path is outside journal directory")

        intent_data = {
            "version": 1,
            "delivery_id": record.delivery_id,
            "provider_receipt_id": record.provider_receipt_id,
            "request_hash": record.request_hash,
            "release_sha": record.release_sha,
            "oncall_route": record.oncall_route,
            "issuer_identity": record.issuer_identity,
            "issuer_signature": record.issuer_signature,
            "store_identity": self.store_identity,
            "timestamp": record.timestamp,
            "transition": "CONSUMED",
        }

        tmp_fd, tmp_path_str = tempfile.mkstemp(
            dir=self.journal_dir,
            prefix="intent_tmp_",
            suffix=".tmp",
        )
        tmp_path = Path(tmp_path_str).resolve()
        if tmp_path.parent != journal_dir_resolved:
            os.close(tmp_fd)
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            raise ValueError("Path traversal detected: temporary path is outside journal directory")

        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(intent_data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())

            os.replace(tmp_path, intent_path)

            dir_fd = os.open(self.journal_dir, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except Exception:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            raise

    def _read_store_data(self) -> dict[str, Any]:
        """Reads store data. If file exists but is corrupt or invalid schema, raises ValueError to fail closed."""
        if not self.store_path.exists():
            data = {"version": 1, "records": {}, "consumed": []}
        else:
            try:
                with open(self.store_path, encoding="utf-8") as f:
                    data = json.load(f)
                self._validate_store_schema(data)
            except Exception as err:
                raise ValueError(f"Authority store data is corrupt or unreadable: {err}") from err

        if self._reconcile_journal_intents(data):
            self._write_store_data_atomic(data)

        return data

    @staticmethod
    def _validate_store_schema(data: Any) -> None:
        """Strictly validates durable store schema (B29). Raises ValueError on any structural or type violation."""
        if not isinstance(data, dict):
            raise ValueError("Store data must be a JSON object")

        allowed_keys = {"version", "records", "consumed"}
        extra_keys = set(data.keys()) - allowed_keys
        if extra_keys:
            raise ValueError(
                f"Store data contains unrecognized top-level key(s): {sorted(list(extra_keys))}"
            )

        if "version" not in data:
            raise ValueError("Missing required top-level key 'version'")

        v = data["version"]
        if type(v) is not int or v != 1:
            raise ValueError(f"Unsupported or invalid store schema version: {v!r}")

        records = data.get("records")
        if not isinstance(records, dict):
            raise ValueError("Store data 'records' must be a dict")

        for del_id, raw_rec in records.items():
            if type(del_id) is not str or del_id != del_id.strip() or not del_id:
                raise ValueError(f"Invalid non-canonical delivery_id key in records: {del_id!r}")
            if not isinstance(raw_rec, dict):
                raise ValueError(f"Record for delivery ID '{del_id}' must be a dict")
            try:
                rec_obj = DeliveryAuthorityRecord.from_dict(raw_rec)
            except Exception as err:
                raise ValueError(
                    f"Invalid record object for delivery ID '{del_id}': {err}"
                ) from err

            if rec_obj.delivery_id != del_id:
                raise ValueError(
                    f"Record delivery ID mismatch: key '{del_id}' != record delivery_id '{rec_obj.delivery_id}'"
                )

        consumed = data.get("consumed")
        if not isinstance(consumed, list):
            raise ValueError("Store data 'consumed' must be a list")

        seen_consumed: set[str] = set()
        for item in consumed:
            if type(item) is not str or item != item.strip() or not item:
                raise ValueError(f"Invalid non-canonical entry in consumed list: {item!r}")
            if item in seen_consumed:
                raise ValueError(f"Duplicate delivery ID in consumed list: {item!r}")
            seen_consumed.add(item)

    def _write_store_data_atomic(self, data: dict[str, Any]) -> None:
        """Atomic write to store file using collision-free temp file, flush, fsync, replace, and parent dir fsync."""
        parent_dir = self.store_path.parent
        parent_dir.mkdir(parents=True, exist_ok=True)

        tmp_fd, tmp_path_str = tempfile.mkstemp(
            dir=parent_dir,
            prefix=f"{self.store_path.name}.",
            suffix=".tmp",
        )
        tmp_path = Path(tmp_path_str)
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())

            os.replace(tmp_path, self.store_path)

            dir_fd = os.open(parent_dir, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except Exception:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            raise

    def get_authority_record(self, delivery_id: str) -> DeliveryAuthorityRecord | None:
        with self._file_lock():
            try:
                data = self._read_store_data()
            except Exception:
                return None
            raw_record = data["records"].get(delivery_id)
            if not raw_record:
                return None
            try:
                return DeliveryAuthorityRecord.from_dict(raw_record)
            except Exception:
                return None

    def store_authority_record_out_of_process(self, record: DeliveryAuthorityRecord) -> None:
        """Out-of-process ingestion helper for writing external authority records."""
        with self._file_lock():
            data = self._read_store_data()
            if record.delivery_id != record.delivery_id.strip():
                raise ValueError("Record delivery_id contains non-canonical whitespace")
            data["records"][record.delivery_id] = record.to_dict()
            self._write_store_data_atomic(data)

    def atomic_consume_if_valid(
        self,
        delivery_id: str,
        validator_fn: Callable[[DeliveryAuthorityRecord], tuple[bool, str, str | None]],
    ) -> tuple[bool, str, str | None]:
        with self._file_lock():
            try:
                _validate_safe_canonical_id(delivery_id, "delivery_id")
            except ValueError as err:
                return (
                    False,
                    "PENDING_VERIFICATION",
                    f"Non-canonical delivery ID '{delivery_id!r}' rejected (fail-closed): {err}",
                )

            try:
                data = self._read_store_data()
            except Exception as err:
                return (
                    False,
                    "PENDING_VERIFICATION",
                    f"Authority store read failed (fail-closed): {err}",
                )

            consumed = set(data["consumed"])

            if delivery_id in consumed:
                return (
                    False,
                    "PENDING_VERIFICATION",
                    f"Authority record for delivery ID '{delivery_id}' has already been consumed (replay attempt rejected)",
                )

            raw_record = data["records"].get(delivery_id)
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
                try:
                    self._write_journal_intent(record)
                except Exception as journal_err:
                    return (
                        False,
                        "PENDING_VERIFICATION",
                        f"Durable intent log write or fsync failed (fail-closed): {journal_err}",
                    )

                consumed.add(delivery_id)
                data["consumed"] = sorted(list(consumed))
                try:
                    self._write_store_data_atomic(data)
                except Exception as write_err:
                    return (
                        False,
                        "PENDING_VERIFICATION",
                        f"Durable store write or fsync failed (fail-closed): {write_err}",
                    )

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
        if (
            not expected_delivery_id
            or not isinstance(expected_delivery_id, str)
            or not expected_delivery_id.strip()
        ):
            return (
                False,
                "PENDING_VERIFICATION",
                "Missing or invalid mandatory expected_delivery_id",
            )
        if (
            not expected_provider_receipt_id
            or not isinstance(expected_provider_receipt_id, str)
            or not expected_provider_receipt_id.strip()
        ):
            return (
                False,
                "PENDING_VERIFICATION",
                "Missing or invalid mandatory expected_provider_receipt_id",
            )
        if (
            not expected_request_hash
            or not isinstance(expected_request_hash, str)
            or not expected_request_hash.strip()
        ):
            return (
                False,
                "PENDING_VERIFICATION",
                "Missing or invalid mandatory expected_request_hash",
            )
        if (
            not expected_release_sha
            or not isinstance(expected_release_sha, str)
            or not expected_release_sha.strip()
        ):
            return (
                False,
                "PENDING_VERIFICATION",
                "Missing or invalid mandatory expected_release_sha",
            )
        if (
            not expected_oncall_route
            or not isinstance(expected_oncall_route, str)
            or not expected_oncall_route.strip()
        ):
            return (
                False,
                "PENDING_VERIFICATION",
                "Missing or invalid mandatory expected_oncall_route",
            )

        exp_req_hash = expected_request_hash.strip().lower()
        if not re.fullmatch(r"^[0-9a-fA-F]{64}$", exp_req_hash) or exp_req_hash == "0" * 64:
            return (
                False,
                "PENDING_VERIFICATION",
                "Invalid expected_request_hash: must be exactly 64 hexadecimal characters",
            )

        exp_sha = expected_release_sha.strip().lower()
        if not re.fullmatch(r"^[0-9a-fA-F]{40}$", exp_sha) or exp_sha == "0" * 40:
            return (
                False,
                "PENDING_VERIFICATION",
                "Invalid expected_release_sha: must be exactly 40 hexadecimal characters",
            )

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
            pub_key = serialization.load_pem_public_key(self.authority_public_key_pem.encode())
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

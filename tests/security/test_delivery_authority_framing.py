"""Fixture-only authentic signatures cannot authorize ambiguous identifier splits."""

import base64
import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from modules.notifications.domain.authority import (
    CANONICAL_AUTHORITY_ISSUER_IDENTITY,
    DeliveryAuthorityReadback,
    DeliveryAuthorityRecord,
    FileDeliveryAuthorityStore,
    verify_journal_intent_record,
)


def _payload(record):
    return (
        f"authority_record:{record.delivery_id}:{record.provider_receipt_id}:"
        f"{record.request_hash}:{record.release_sha}:{record.oncall_route}:"
        f"{record.timestamp}:{record.issuer_identity}"
    ).encode()


def _verify(readback, record):
    values = record.to_dict() if isinstance(record, DeliveryAuthorityRecord) else record
    return readback.verify_authority_record(
        record=record,
        expected_delivery_id=values["delivery_id"],
        expected_provider_receipt_id=values["provider_receipt_id"],
        expected_request_hash=values["request_hash"],
        expected_release_sha=values["release_sha"],
        expected_oncall_route=values["oncall_route"],
    )


@pytest.mark.parametrize("split", ["delivery", "receipt"])
@pytest.mark.parametrize("path", ["dict", "direct", "store", "persisted", "journal", "writer"])
def test_authentic_signature_cannot_rebind_delimiter_identifiers(tmp_path, split, path):
    key = Ed25519PrivateKey.generate()  # Fixture ONLY, never a production trust root.
    public = (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    base = DeliveryAuthorityRecord(
        "del-a",
        "receipt-1",
        "a" * 64,
        "b" * 40,
        "ops-lead",
        datetime.now(UTC).isoformat(),
        CANONICAL_AUTHORITY_ISSUER_IDENTITY,
        "",
    )
    valid = replace(base, issuer_signature=base64.b64encode(key.sign(_payload(base))).decode())

    # Match the existing test-only boundary pattern; production constructor and
    # pinned key are unchanged, with no environment/global trust-root fallback.
    class FixtureReadback(DeliveryAuthorityReadback):
        def __init__(self):
            self.authority_public_key_pem = public
            self.allowed_issuer_identity = CANONICAL_AUTHORITY_ISSUER_IDENTITY
            self.authority_store = None

    reader = FixtureReadback()
    assert _verify(reader, valid) == (True, "DELIVERED", None)
    first = replace(base, delivery_id="del-a:part")
    second = replace(base, provider_receipt_id="part:receipt-1")
    assert _payload(first) == _payload(second)
    signature = key.sign(_payload(first))
    key.public_key().verify(signature, _payload(second))  # Actually authentic same bytes.
    ambiguous = replace(
        first if split == "delivery" else second,
        issuer_signature=base64.b64encode(signature).decode(),
    )
    store = FileDeliveryAuthorityStore(tmp_path / "authority.json", authority_public_key_pem=public)
    before = store.store_path.read_bytes()
    if path in ("dict", "direct"):
        result = _verify(reader, ambiguous.to_dict() if path == "dict" else ambiguous)
        assert result[0] is False and result[1] == "PENDING_VERIFICATION"
        assert "signed identifier" in result[2]
    elif path == "store":
        with pytest.raises(ValueError, match="signed identifier"):
            store._store_authority_record_for_testing(ambiguous)
    elif path == "writer":
        with pytest.raises(ValueError, match="signed identifier"):
            store._write_journal_intent(ambiguous)
    elif path == "journal":
        intent = {
            **ambiguous.to_dict(),
            "version": 1,
            "store_identity": store.store_identity,
            "transition": "CONSUMED",
        }
        with pytest.raises(ValueError, match="signed identifier"):
            verify_journal_intent_record(intent, store.store_identity, ambiguous, public)
    else:
        store.store_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "records": {ambiguous.delivery_id: ambiguous.to_dict()},
                    "consumed": [],
                }
            )
        )
        before = store.store_path.read_bytes()
        assert store.get_authority_record(ambiguous.delivery_id) is None
        result = store.atomic_consume_if_valid(
            ambiguous.delivery_id, lambda r: (True, "DELIVERED", None)
        )
        assert result[0] is False and result[1] == "PENDING_VERIFICATION"
    assert store.store_path.read_bytes() == before
    assert list(store.journal_dir.iterdir()) == []

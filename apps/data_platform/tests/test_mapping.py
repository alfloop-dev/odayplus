from __future__ import annotations

from datetime import UTC, datetime

import pytest

from apps.data_platform.contracts import QuarantineReason, SourceKind
from apps.data_platform.identifiers import (
    brand_id_for_merchant,
    machine_id_for_device,
    store_id_for_place,
    tenant_id_for_merchant,
)
from apps.data_platform.mapping import (
    MachineIdentity,
    MerchantIdentity,
    MissingMappingError,
    SourceContractError,
    StoreIdentity,
    project_device,
    project_daily_statistic,
    project_domain_input,
    project_forecast_input,
    project_learning_import,
    project_machine_status_event,
    project_merchant,
    project_place,
    project_transaction,
)
from apps.data_platform.status_mapping import StatusMappingContract


class Lookup:
    def __init__(self) -> None:
        self.merchants = {
            "merchant-1": MerchantIdentity(
                "merchant-1",
                tenant_id_for_merchant("merchant-1"),
                brand_id_for_merchant("merchant-1"),
            ),
            "merchant-2": MerchantIdentity(
                "merchant-2",
                tenant_id_for_merchant("merchant-2"),
                brand_id_for_merchant("merchant-2"),
            ),
        }
        self.places = {
            "place-1": StoreIdentity(
                "place-1",
                "merchant-1",
                tenant_id_for_merchant("merchant-1"),
                brand_id_for_merchant("merchant-1"),
                store_id_for_place("place-1"),
            ),
        }
        self.devices = {
            "device-1": MachineIdentity(
                "device-1",
                tenant_id_for_merchant("merchant-1"),
                store_id_for_place("place-1"),
                machine_id_for_device("device-1"),
            )
        }

    def require_merchant(self, source_merchant_id: str) -> MerchantIdentity:
        try:
            return self.merchants[source_merchant_id]
        except KeyError as exc:
            raise MissingMappingError(
                QuarantineReason.MISSING_MERCHANT_MAPPING, "missing merchant"
            ) from exc

    def require_place(self, source_place_id: str) -> StoreIdentity:
        try:
            return self.places[source_place_id]
        except KeyError as exc:
            raise MissingMappingError(
                QuarantineReason.MISSING_PLACE_MAPPING, "missing place"
            ) from exc

    def require_device(self, source_device_id: str) -> MachineIdentity:
        try:
            return self.devices[source_device_id]
        except KeyError as exc:
            raise MissingMappingError(
                QuarantineReason.MISSING_DEVICE_MAPPING, "missing device"
            ) from exc


def _merchant_document() -> dict[str, object]:
    return {
        "_id": "merchant-1",
        "companyName": "Real Merchant",
        "country": "TW",
        "currency": "TWD",
        "operation": "active",
        "createdAt": "2022-01-01T00:00:00Z",
    }


def _place_document() -> dict[str, object]:
    return {
        "_id": "place-1",
        "title": "Real Place",
        "address": "Taipei",
        "geolocation": {"coordinates": [121.5, 25.04]},
        "merchant": {"_id": "merchant-1"},
        "operation": "active",
        "publish": True,
        "type": "laundry",
        "createdAt": "2022-01-02T00:00:00Z",
    }


def test_merchant_mapping_is_deterministic(envelope_factory) -> None:
    first = project_merchant(
        envelope_factory(SourceKind.MERCHANT, _merchant_document())
    )
    second = project_merchant(
        envelope_factory(SourceKind.MERCHANT, _merchant_document())
    )
    assert first == second
    assert first.tenant_id == tenant_id_for_merchant("merchant-1")
    assert first.brand_id == brand_id_for_merchant("merchant-1")


@pytest.mark.parametrize(
    "document",
    [
        {"_id": "bad", "companyName": "test merchant"},
        {
            "_id": "bad",
            "companyName": "test merchant",
            "country": "TW",
            "currency": "TWD",
        },
    ],
)
def test_malformed_merchant_is_typed_quarantine(envelope_factory, document) -> None:
    with pytest.raises(SourceContractError) as error:
        project_merchant(envelope_factory(SourceKind.MERCHANT, document))
    assert error.value.reason_code is QuarantineReason.MISSING_REQUIRED_FIELD


def test_place_without_created_at_is_quarantined(envelope_factory) -> None:
    document = _place_document()
    del document["createdAt"]
    with pytest.raises(SourceContractError) as error:
        project_place(envelope_factory(SourceKind.PLACE, document), Lookup())
    assert error.value.reason_code is QuarantineReason.MISSING_REQUIRED_FIELD


def test_place_without_resolvable_merchant_does_not_create_orphan(envelope_factory) -> None:
    document = _place_document()
    document["merchant"] = {"_id": "missing"}
    with pytest.raises(MissingMappingError) as error:
        project_place(envelope_factory(SourceKind.PLACE, document), Lookup())
    assert error.value.reason_code is QuarantineReason.MISSING_MERCHANT_MAPPING


def test_numeric_merchant_operation_requires_owner_mapping(envelope_factory) -> None:
    document = _merchant_document()
    document["operation"] = 1
    with pytest.raises(SourceContractError) as error:
        project_merchant(envelope_factory(SourceKind.MERCHANT, document))
    assert (
        error.value.reason_code
        is QuarantineReason.OPERATION_MAPPING_UNAPPROVED
    )


def test_numeric_place_operation_and_type_use_approved_contract(
    envelope_factory,
) -> None:
    contract = StatusMappingContract(
        version="expansion-approved-v1",
        approved_by="expansion-data-owner",
        approved_at=datetime(2026, 7, 24, tzinfo=UTC),
        mappings={
            "place_operation": {"1": "open"},
            "place_type": {"2": "laundry"},
        },
    )
    contract.validate()
    document = _place_document()
    document["operation"] = 1
    document["type"] = 2
    projection = project_place(
        envelope_factory(SourceKind.PLACE, document), Lookup(), contract
    )
    assert projection.store_status == "open"
    assert projection.store_format_code == "laundry"


def test_numeric_place_type_without_mapping_is_quarantined(envelope_factory) -> None:
    document = _place_document()
    document["type"] = 99
    with pytest.raises(SourceContractError) as error:
        project_place(envelope_factory(SourceKind.PLACE, document), Lookup())
    assert error.value.reason_code is QuarantineReason.TYPE_MAPPING_UNAPPROVED


def test_missing_place_geography_is_preserved_without_synthesis(
    envelope_factory,
) -> None:
    document = _place_document()
    document.pop("address")
    document.pop("geolocation")
    projection = project_place(
        envelope_factory(SourceKind.PLACE, document), Lookup()
    )
    assert projection.address_id is None
    assert projection.raw_address is None
    assert projection.latitude is None
    assert projection.longitude is None


def test_device_enforces_merchant_place_tenant(envelope_factory) -> None:
    document = {
        "_id": "device-1",
        "id": "device-1",
        "hwid": "HW-1",
        "merchant": {"_id": "merchant-2"},
        "place": {"_id": "place-1"},
        "product": {"_id": "product-1"},
        "model": "WASH-1",
        "machineType": "washer",
        "modelStatus": "online",
        "createdAt": "2022-01-03T00:00:00Z",
    }
    with pytest.raises(MissingMappingError) as error:
        project_device(envelope_factory(SourceKind.DEVICE, document), Lookup())
    assert error.value.reason_code is QuarantineReason.TENANT_OWNERSHIP_MISMATCH


@pytest.mark.parametrize(
    ("enabled", "connection", "expected"),
    (
        (True, False, "active"),
        (True, True, "active"),
        (False, True, "inactive"),
        (False, False, "inactive"),
    ),
)
def test_device_uses_live_enable_for_lifecycle_not_nested_model_status(
    envelope_factory,
    enabled,
    connection,
    expected,
) -> None:
    document = {
        "_id": "device-live-1",
        "id": "device-live-1",
        "hwid": "HW-LIVE-1",
        "merchant": {"_id": "merchant-1"},
        "place": {"_id": "place-1"},
        "product": {"_id": "product-1"},
        "model": "WASH-1",
        "machineType": 0,
        "enable": enabled,
        "connection": connection,
        "modelStatus": {
            "hwType": "pulse",
            "operationStatus": {"machineStatus": "611"},
        },
        "createdAt": "2022-01-03T00:00:00Z",
    }

    projection = project_device(
        envelope_factory(SourceKind.DEVICE, document),
        Lookup(),
    )

    assert projection.machine_status == expected


def test_device_without_live_lifecycle_field_is_quarantined(envelope_factory) -> None:
    document = {
        "_id": "device-live-2",
        "id": "device-live-2",
        "hwid": "HW-LIVE-2",
        "merchant": {"_id": "merchant-1"},
        "place": {"_id": "place-1"},
        "product": {"_id": "product-1"},
        "model": "WASH-1",
        "machineType": 0,
        "modelStatus": {"operationStatus": {"machineStatus": "611"}},
        "createdAt": "2022-01-03T00:00:00Z",
    }

    with pytest.raises(SourceContractError) as error:
        project_device(envelope_factory(SourceKind.DEVICE, document), Lookup())

    assert error.value.reason_code is QuarantineReason.MISSING_REQUIRED_FIELD


def test_daily_statistic_uses_live_datetime_fields(envelope_factory) -> None:
    document = {
        "_id": "daily-1",
        "merchant": {"_id": "merchant-1"},
        "place": {"_id": "place-1"},
        "device": {"_id": "device-1"},
        "startDatetime": "2025-06-01T00:00:00Z",
        "endDatetime": "2025-06-02T00:00:00Z",
        "amount": 1234,
        "count": 9,
        "gateway": "cash",
    }
    projection = project_daily_statistic(
        envelope_factory(SourceKind.DEVICE_DAILY_STATISTICS, document), Lookup()
    )
    assert projection.period_start.isoformat() == "2025-06-01T00:00:00+00:00"
    assert projection.period_end.isoformat() == "2025-06-02T00:00:00+00:00"


def test_forecast_horizon_is_legacy_external_output(envelope_factory) -> None:
    document = {
        "_id": "forecast-1",
        "merchant": {"_id": "merchant-1"},
        "place": {"_id": "place-1"},
        "date": "2026-07-26T00:00:00Z",
        "predict": 888.25,
    }
    projection = project_forecast_input(
        envelope_factory(SourceKind.AI_REVENUE_STATS, document), Lookup()
    )
    assert projection.predicted_value == 888.25
    assert not hasattr(projection, "model_version")
    assert not hasattr(projection, "run_id")


def test_kmeans_preserves_feature_snapshot_and_label_list(envelope_factory) -> None:
    document = {
        "_id": "segment-1",
        "account": "sensitive-account-reference",
        "merchant": {"_id": "merchant-1"},
        "runDate": "2026-07-24T00:00:00Z",
        "featureSnapshot": {
            "avgTicket": 100,
            "extra_dry_ratio": 0.2,
            "prefer_temperature": 40,
            "totalAmount": 1000,
            "transCount": 10,
        },
        "segmentId": "segment-a",
        "segmentLabel": ["high_value", "warm_wash"],
    }
    projection = project_learning_import(
        envelope_factory(SourceKind.AI_CONSUMER_KMEANS_V1, document), Lookup()
    )
    assert projection.segment_labels == ("high_value", "warm_wash")
    assert projection.source_account_ref_hash != document["account"]
    assert set(projection.feature_snapshot) == set(document["featureSnapshot"])


@pytest.mark.parametrize(
    ("kind", "document", "nested_field"),
    [
        (
            SourceKind.PRODUCT,
            {
                "_id": "product-1",
                "merchant": {"_id": "merchant-1"},
                "title": "Washer program",
                "details": {"price": 80, "unknown": {"mode": "warm"}},
                "createdAt": "2026-07-23T00:00:00Z",
            },
            "details",
        ),
        (
            SourceKind.PRODUCTS,
            {
                "_id": "products-1",
                "merchant": {"_id": "merchant-1"},
                "name": "Template product",
                "category": "wash",
                "country": "TW",
                "publish": True,
                "template": {"unknown": {"minutes": 40}},
                "createdAt": "2026-07-23T00:00:00Z",
            },
            "template",
        ),
        (
            SourceKind.PROMOTIONS,
            {
                "_id": "promotion-1",
                "merchant": {"_id": "merchant-1"},
                "enabled": True,
                "start": "2026-07-23T00:00:00Z",
                "end": "2026-07-30T00:00:00Z",
                "rule": {"unknown": {"threshold": 2}},
                "createdAt": "2026-07-23T00:00:00Z",
            },
            "rule",
        ),
    ],
)
def test_commercial_nested_payload_is_opaque_not_reinterpreted(
    envelope_factory, kind, document, nested_field
) -> None:
    projection = project_domain_input(
        envelope_factory(kind, document), Lookup()
    )
    assert projection.input_payload[nested_field] == document[nested_field]


def test_campaign_mapping_uses_live_offer_fields(envelope_factory) -> None:
    document = {
        "_id": "campaign-1",
        "merchant": {"_id": "merchant-1"},
        "offerName": "Summer offer",
        "isActive": True,
        "startDatetime": "2026-07-23T00:00:00Z",
        "endDatetime": "2026-07-30T00:00:00Z",
        "discountAmount": 20,
        "discountPercentage": 0,
        "offerType": "amount",
        "offerMethod": "automatic",
        "createdAt": "2026-07-20T00:00:00Z",
    }
    projection = project_domain_input(
        envelope_factory(SourceKind.CAMPAIGN, document), Lookup()
    )
    assert projection.effective_at.isoformat() == "2026-07-23T00:00:00+00:00"
    assert projection.input_payload["offerName"] == "Summer offer"


def test_device_error_log_maps_to_defensible_error_evidence(
    envelope_factory,
) -> None:
    document = {
        "_id": "log-1",
        "merchant": {"_id": "merchant-1"},
        "place": {"_id": "place-1"},
        "device": {"_id": "device-1"},
        "logType": "error",
        "logData": {"errCode": "E-42"},
        "createdAt": "2026-07-23T00:00:00Z",
    }
    projection = project_machine_status_event(
        envelope_factory(SourceKind.DEVICE_LOG, document), Lookup()
    )
    assert projection.status_type == "error"
    assert projection.error_code == "E-42"


def test_device_connection_log_requires_owner_mapping(envelope_factory) -> None:
    document = {
        "_id": "log-2",
        "device": {"_id": "device-1"},
        "logType": "connection",
        "logData": {"state": "1"},
        "createdAt": "2026-07-23T00:00:00Z",
    }
    with pytest.raises(SourceContractError) as error:
        project_machine_status_event(
            envelope_factory(SourceKind.DEVICE_LOG, document), Lookup()
        )
    assert (
        error.value.reason_code
        is QuarantineReason.CONNECTION_MAPPING_UNAPPROVED
    )


def test_device_connection_log_uses_approved_mapping(envelope_factory) -> None:
    contract = StatusMappingContract(
        version="ops-approved-v1",
        approved_by="store-ops-owner",
        approved_at=datetime(2026, 7, 24, tzinfo=UTC),
        mappings={"device_connection": {"1": "online"}},
    )
    contract.validate()
    document = {
        "_id": "log-3",
        "device": {"_id": "device-1"},
        "logType": "connection",
        "logData": {"state": "1"},
        "createdAt": "2026-07-23T00:00:00Z",
    }
    projection = project_machine_status_event(
        envelope_factory(SourceKind.DEVICE_LOG, document), Lookup(), contract
    )
    assert projection.status_type == "online"


def test_numeric_transaction_status_requires_approved_contract(envelope_factory) -> None:
    document = {
        "_id": "tx-1",
        "transactionId": "tx-1",
        "merchant": {"_id": "merchant-1"},
        "place": {"_id": "place-1"},
        "amount": 100,
        "amountPaid": 100,
        "currency": "TWD",
        "payGateway": "card",
        "status": "0",
        "createdAt": "2026-07-23T00:00:00Z",
    }
    with pytest.raises(SourceContractError) as error:
        project_transaction(
            envelope_factory(SourceKind.TRANSACTION, document), Lookup()
        )
    assert error.value.reason_code is QuarantineReason.STATUS_MAPPING_UNAPPROVED


def test_versioned_status_contract_enables_numeric_transaction(envelope_factory) -> None:
    contract = StatusMappingContract(
        version="governance-approved-v1",
        approved_by="data-governance",
        approved_at=datetime(2026, 7, 24, tzinfo=UTC),
        mappings={"transaction": {"0": "succeeded"}},
    )
    contract.validate()
    document = {
        "_id": "tx-1",
        "transactionId": "tx-1",
        "merchant": {"_id": "merchant-1"},
        "place": {"_id": "place-1"},
        "amount": 100,
        "amountPaid": 100,
        "currency": "TWD",
        "payGateway": "card",
        "status": "0",
        "createdAt": "2026-07-23T00:00:00Z",
    }
    projection = project_transaction(
        envelope_factory(SourceKind.TRANSACTION, document), Lookup(), contract
    )
    assert projection.transaction_status == "succeeded"


def test_transaction_status_is_never_inferred_from_equal_amounts(
    envelope_factory,
) -> None:
    document = {
        "_id": "tx-no-status",
        "transactionId": "tx-no-status",
        "merchant": {"_id": "merchant-1"},
        "place": {"_id": "place-1"},
        "amount": 100,
        "amountPaid": 100,
        "currency": "TWD",
        "payGateway": "card",
        "createdAt": "2026-07-23T00:00:00Z",
    }
    with pytest.raises(SourceContractError) as error:
        project_transaction(
            envelope_factory(SourceKind.TRANSACTION, document), Lookup()
        )
    assert error.value.reason_code is QuarantineReason.MISSING_REQUIRED_FIELD


def test_orders_explicit_state_is_authoritative_without_numeric_guess(envelope_factory) -> None:
    document = {
        "_id": "order-1",
        "orderId": "order-1",
        "merchant": {"_id": "merchant-1"},
        "place": {"_id": "place-1"},
        "amount": 100,
        "currency": "TWD",
        "state": "TRADE_SUCCESS",
        "payment": {
            "payGateway": "card",
            "provider": "provider",
            "result": {},
        },
        "createdAt": "2026-07-23T00:00:00Z",
    }
    projection = project_transaction(
        envelope_factory(SourceKind.ORDERS, document), Lookup()
    )
    assert projection.transaction_status == "succeeded"
    assert projection.net_amount == 100
    assert projection.payment_method == "card"


def test_unpaid_order_uses_authoritative_state_and_zero_paid_amount(
    envelope_factory,
) -> None:
    document = {
        "_id": "order-not-paid-1",
        "orderId": "order-not-paid-1",
        "merchant": {"_id": "merchant-1"},
        "place": {"_id": "place-1"},
        "amount": 100,
        "currency": "TWD",
        "state": "TRADE_NOT_PAY",
        "payment": {"payGateway": "card", "result": {}},
        "createdAt": "2026-07-23T00:00:00Z",
    }

    projection = project_transaction(
        envelope_factory(SourceKind.ORDERS, document), Lookup()
    )

    assert projection.transaction_status == "failed"
    assert projection.gross_amount == 100
    assert projection.net_amount == 0
    assert projection.payment_time is None


def test_order_id_links_authoritative_order_and_transaction(envelope_factory) -> None:
    contract = StatusMappingContract(
        version="governance-approved-v1",
        approved_by="data-governance",
        approved_at=datetime(2026, 7, 24, tzinfo=UTC),
        mappings={"transaction": {"0": "succeeded"}},
    )
    contract.validate()
    order = {
        "_id": "mongo-order-row",
        "orderId": "shared-order-1",
        "merchant": {"_id": "merchant-1"},
        "place": {"_id": "place-1"},
        "amount": 100,
        "currency": "TWD",
        "state": "TRADE_SUCCESS",
        "payment": {"payGateway": "card", "provider": "provider", "result": {}},
        "createdAt": "2026-07-23T00:00:00Z",
    }
    transaction = {
        "_id": "mongo-transaction-row",
        "transactionId": "gateway-transaction-1",
        "orderId": "shared-order-1",
        "merchant": {"_id": "merchant-1"},
        "place": {"_id": "place-1"},
        "amount": 100,
        "amountPaid": 100,
        "currency": "TWD",
        "payGateway": "card",
        "status": "0",
        "createdAt": "2026-07-23T00:00:00Z",
    }
    order_projection = project_transaction(
        envelope_factory(SourceKind.ORDERS, order), Lookup()
    )
    transaction_projection = project_transaction(
        envelope_factory(SourceKind.TRANSACTION, transaction),
        Lookup(),
        contract,
    )
    assert order_projection.source_id == "shared-order-1"
    assert (
        order_projection.transaction_id
        == transaction_projection.transaction_id
    )


@pytest.mark.parametrize(
    ("event_time", "reason"),
    [
        ("1970-01-01T00:00:00Z", QuarantineReason.EVENT_TIME_EPOCH_OUTLIER),
        ("2217-10-10T00:00:00Z", QuarantineReason.EVENT_TIME_FUTURE_OUTLIER),
    ],
)
def test_event_time_outliers_never_enter_transaction_training(
    envelope_factory, event_time, reason
) -> None:
    document = {
        "_id": event_time,
        "orderId": event_time,
        "merchant": {"_id": "merchant-1"},
        "place": {"_id": "place-1"},
        "amount": 100,
        "currency": "TWD",
        "state": "TRADE_SUCCESS",
        "payment": {"amount": 100, "gateway": "card", "currency": "TWD"},
        "createdAt": event_time,
    }
    with pytest.raises(SourceContractError) as error:
        project_transaction(
            envelope_factory(SourceKind.ORDERS, document), Lookup()
        )
    assert error.value.reason_code is reason

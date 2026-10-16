import base64
from types import SimpleNamespace

import pytest

from src.reconciliation_engine import DISCREPANCY_FLAGGED
from src.supabase_data import (
    SOURCE_COLUMNS,
    queue_dispute_email,
    reconcile_supabase_data,
    update_invoice_audit,
)


class FakeReadTable:
    def __init__(self, rows):
        self.rows = rows

    def select(self, _columns):
        return self

    def range(self, _start, _end):
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows)


class FakeReadClient:
    def __init__(self, tables):
        self.tables = tables
        self.requested_tables = []

    def table(self, name):
        self.requested_tables.append(name)
        return FakeReadTable(self.tables[name])


class FakeUpdateTable:
    def __init__(self, response):
        self.response = response
        self.values = None
        self.filters = []
        self.selected = None
        self.in_filters = []

    def update(self, values):
        self.values = values
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def in_(self, column, values):
        self.in_filters.append((column, values))
        return self

    def select(self, columns):
        self.selected = columns
        return self

    def execute(self):
        return SimpleNamespace(data=self.response)


class FakeUpdateClient:
    def __init__(self, response):
        self.table_instance = FakeUpdateTable(response)

    def table(self, name):
        assert name == "carrier_invoices"
        return self.table_instance


class FakeRpcClient:
    def __init__(self, response):
        self.response = response
        self.function_name = None
        self.params = None

    def rpc(self, function_name, params):
        self.function_name = function_name
        self.params = params
        return FakeRpcCall(self.response)


class FakeRpcCall:
    def __init__(self, response):
        self.response = response

    def execute(self):
        return SimpleNamespace(data=self.response)


def test_source_columns_include_extended_document_fields():
    assert {
        "bill_to_name",
        "bill_to_address",
        "remit_to_name",
        "remit_to_address",
        "payment_terms",
        "payment_due_date",
        "pickup_date",
        "delivery_date",
        "freight_class",
        "nmfc_code",
        "rate_tariff_reference",
        "rate_per_cwt",
        "handling_unit_count",
        "handling_unit_type",
        "source_document_hash",
        "dispute_date",
        "rebuttal_deadline",
    }.issubset(SOURCE_COLUMNS["carrier_invoices"])
    assert {
        "incoterms",
        "freight_charge_terms",
        "payment_terms",
        "equipment_type_required",
        "temperature_controlled",
        "liftgate_required",
        "scheduled_pickup_window",
        "scheduled_delivery_window",
        "buyer_contact_email",
    }.issubset(SOURCE_COLUMNS["purchase_orders"])
    assert {
        "driver_name",
        "driver_cdl_number",
        "carrier_scac",
        "tractor_unit_number",
        "security_seal_number",
        "security_seal_status",
        "scale_operator_id",
        "scale_calibration_date",
        "yard_check_in_timestamp",
        "dock_unloaded_timestamp",
        "yard_departure_timestamp",
        "received_pallet_count",
        "packaging_condition",
    }.issubset(SOURCE_COLUMNS["warehouse_goods_receipts"])
    assert all(
        "carrier_email" in SOURCE_COLUMNS[table]
        for table in (
            "carrier_invoices",
            "purchase_orders",
            "warehouse_goods_receipts",
        )
    )
    assert not {
        "dispute_from_email",
        "dispute_to_email",
        "dispute_cc_email",
        "dispute_subject",
        "dispute_memo_body",
        "email_dispatch_status",
    }.intersection(SOURCE_COLUMNS["carrier_invoices"])


def test_reconcile_supabase_data_uses_all_three_source_tables():
    client = FakeReadClient(
        {
            "purchase_orders": [{
                "po_number": "PO-1", "vendor_id": "V-1", "carrier_name": "Carrier",
                "origin": "A", "destination": "B", "contracted_base_freight": 1000,
                "agreed_fuel_surcharge": 100, "max_accessorial_allowance": 50,
            }],
            "carrier_invoices": [{
                "invoice_number": "INV-1", "po_number": "PO-1", "carrier_name": "Carrier",
                "billed_base_freight": 1100, "billed_fuel_surcharge": 100,
                "billed_accessorial_fee": 50, "billed_weight_lbs": 1000,
                "invoice_date": "2026-09-01",
            }],
            "warehouse_goods_receipts": [{
                "po_number": "PO-1", "delivery_timestamp": "2026-09-01T10:00:00",
                "dock_signature": "Receiver", "actual_scale_weight_lbs": 1000,
            }],
        }
    )

    result = reconcile_supabase_data(client)

    assert client.requested_tables == [
        "purchase_orders", "carrier_invoices", "warehouse_goods_receipts"
    ]
    assert result.loc[0, "status"] == DISCREPANCY_FLAGGED
    assert result.loc[0, "base_variance"] == 100


def test_update_invoice_audit_writes_resolution_by_invoice_id():
    client = FakeUpdateClient([{"invoice_id": "INV-1"}])

    update_invoice_audit(
        client,
        "INV-1",
        "CLOSED_RESOLVED",
        "Unapproved accessorial; dispute sent.",
    )

    assert client.table_instance.filters == [("invoice_id", "INV-1")]
    values = client.table_instance.values
    assert values is not None
    assert values["audit_status"] == "CLOSED_RESOLVED"
    assert values["audit_note"] == "Unapproved accessorial; dispute sent."
    assert values["audited_at"]
    assert "auditor_name" not in values


def test_update_invoice_audit_allows_empty_resolution_note():
    client = FakeUpdateClient([{"invoice_id": "INV-1"}])

    update_invoice_audit(client, "INV-1", "UNDER_REVIEW", "")

    values = client.table_instance.values
    assert values is not None
    assert values["audit_status"] == "UNDER_REVIEW"
    assert values["audit_note"] == ""


def test_update_invoice_audit_accepts_dispute_lifecycle_states():
    client = FakeUpdateClient([{"invoice_id": "INV-1"}])

    update_invoice_audit(client, "INV-1", "REBUTTAL_RECEIVED", "Carrier sent a POD.")

    assert client.table_instance.values["audit_status"] == "REBUTTAL_RECEIVED"


def test_disputed_status_is_only_set_by_confirmed_dispatch():
    with pytest.raises(ValueError, match="Unsupported audit status"):
        update_invoice_audit(
            FakeUpdateClient([]), "INV-1", "DISPUTED", "Email not confirmed."
        )


def test_update_invoice_audit_rejects_missing_invoice():
    client = FakeUpdateClient([])

    try:
        update_invoice_audit(client, "INV-404", "CLOSED_RESOLVED", "Reason")
    except LookupError as error:
        assert "INV-404" in str(error)
    else:
        raise AssertionError("A missing invoice should not report a successful write.")


def test_queue_dispute_email_calls_atomic_supabase_dispatch_function():
    client = FakeRpcClient("dispatch-123")

    dispatch_id = queue_dispute_email(
        client,
        "INV-1",
        "Northstar Freight",
        to_email="billing@example.com",
        cc_email="audit@example.com",
        subject="Billing dispute: INV-1",
        body="Please review the attached dispute memo.",
    )

    assert dispatch_id == "dispatch-123"
    assert client.function_name == "queue_dispute_dispatch"
    assert client.params == {
        "p_invoice_id": "INV-1",
        "p_carrier_name": "Northstar Freight",
        "p_dispute_to_email": "billing@example.com",
        "p_dispute_cc_email": "audit@example.com",
        "p_dispute_subject": "Billing dispute: INV-1",
        "p_dispute_memo_body": "Please review the attached dispute memo.",
        "p_dispute_evidence_pdf_base64": None,
        "p_dispute_evidence_filename": None,
        "p_dispute_from_email": "",
    }


def test_queue_dispute_email_includes_configured_sender():
    client = FakeRpcClient("dispatch-123")

    queue_dispute_email(
        client,
        "INV-1",
        "Northstar Freight",
        to_email="billing@example.com",
        cc_email="",
        from_email="audit@example.com",
        subject="Billing dispute: INV-1",
        body="Please review the dispute memo.",
    )

    assert client.params["p_dispute_from_email"] == "audit@example.com"


def test_queue_dispute_email_encodes_verified_pdf_attachment():
    client = FakeRpcClient("dispatch-123")
    packet = b"%PDF-1.4 evidence"

    queue_dispute_email(
        client,
        "INV-1",
        "Northstar Freight",
        to_email="billing@example.com",
        cc_email="",
        subject="Billing dispute: INV-1",
        body="Please review the attached dispute memo.",
        evidence_packet=packet,
        evidence_filename="Dispute_Packet_INV-1.pdf",
    )

    assert client.params["p_dispute_evidence_pdf_base64"] == base64.b64encode(
        packet
    ).decode("ascii")
    assert client.params["p_dispute_evidence_filename"] == "Dispute_Packet_INV-1.pdf"


def test_queue_dispute_email_rejects_non_pdf_evidence():
    client = FakeRpcClient("dispatch-123")

    with pytest.raises(ValueError, match="not a valid PDF"):
        queue_dispute_email(
            client,
            "INV-1",
            "Northstar Freight",
            to_email="billing@example.com",
            cc_email="",
            subject="Billing dispute: INV-1",
            body="Please review the attached dispute memo.",
            evidence_packet=b"not a pdf",
            evidence_filename="Dispute_Packet_INV-1.pdf",
        )


def test_queue_dispute_email_validates_recipient_and_required_content():
    client = FakeRpcClient("dispatch-123")

    try:
        queue_dispute_email(
            client,
            "INV-1",
            "Northstar Freight",
            to_email="",
            cc_email="",
            subject="Dispute",
            body="Memo",
        )
    except ValueError as error:
        assert "To must include" in str(error)
    else:
        raise AssertionError("A dispatch without a To recipient must be rejected.")

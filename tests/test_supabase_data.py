from types import SimpleNamespace

from src.reconciliation_engine import DISCREPANCY_FLAGGED
from src.supabase_data import reconcile_supabase_data, update_invoice_audit


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

    def update(self, values):
        self.values = values
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
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


def test_update_invoice_audit_writes_resolution_and_requires_matching_invoice():
    client = FakeUpdateClient([{"invoice_number": "INV-1"}])

    update_invoice_audit(
        client,
        "INV-1",
        "DISPUTED",
        "Alex Auditor",
        "Unapproved accessorial; dispute sent.",
    )

    assert client.table_instance.filters == [("invoice_number", "INV-1")]
    values = client.table_instance.values
    assert values is not None
    assert values["audit_status"] == "DISPUTED"
    assert values["auditor_name"] == "Alex Auditor"
    assert values["audit_note"] == "Unapproved accessorial; dispute sent."
    assert values["audited_at"]


def test_update_invoice_audit_rejects_missing_invoice():
    client = FakeUpdateClient([])

    try:
        update_invoice_audit(client, "INV-404", "DISPUTED", "Alex", "Reason")
    except LookupError as error:
        assert "INV-404" in str(error)
    else:
        raise AssertionError("A missing invoice should not report a successful write.")

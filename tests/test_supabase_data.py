from types import SimpleNamespace

from src.reconciliation_engine import DISCREPANCY_FLAGGED
from src.supabase_data import reconcile_supabase_data


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


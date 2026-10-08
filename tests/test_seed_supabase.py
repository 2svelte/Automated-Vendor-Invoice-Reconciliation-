import pandas as pd

from src.seed_supabase import seed_supabase_from_csv


def test_seed_supabase_maps_all_csv_sources_to_tables(tmp_path, monkeypatch):
    pd.DataFrame([{
        "po_number": "PO-1", "vendor_id": "V-1", "carrier_name": "Carrier",
        "origin": "A", "destination": "B", "contracted_base_freight": 1000,
        "agreed_fuel_surcharge": 100, "max_accessorial_allowance": 50,
    }]).to_csv(tmp_path / "erp_purchase_orders.csv", index=False)
    pd.DataFrame([{
        "invoice_number": "INV-1", "po_number": "PO-1", "carrier_name": "Carrier",
        "billed_base_freight": 1000, "billed_fuel_surcharge": 100,
        "billed_accessorial_fee": 50, "billed_weight_lbs": 1000,
        "invoice_date": "2026-09-01",
    }]).to_csv(tmp_path / "carrier_invoices_raw.csv", index=False)
    pd.DataFrame([{
        "po_number": "PO-1", "delivery_timestamp": "2026-09-01 10:00",
        "dock_signature": "Receiver", "actual_scale_weight_lbs": 1000,
    }]).to_csv(tmp_path / "warehouse_goods_receipts.csv", index=False)
    captured = []

    class FakeTable:
        def __init__(self, name):
            self.name = name

        def upsert(self, records, on_conflict):
            captured.append((self.name, records, on_conflict))
            return self

        def execute(self):
            return None

    class FakeClient:
        def table(self, name):
            return FakeTable(name)

    monkeypatch.setattr("src.seed_supabase.create_supabase_client", FakeClient)

    counts = seed_supabase_from_csv(tmp_path)

    assert counts == {
        "purchase_orders": 1,
        "carrier_invoices": 1,
        "warehouse_goods_receipts": 1,
    }
    invoice = next(rows[0] for table, rows, _ in captured if table == "carrier_invoices")
    assert invoice["invoice_id"] == invoice["invoice_number"] == "INV-1"
    assert invoice["po_number"] == invoice["shipment_id"] == "PO-1"
    assert invoice["billed_base_freight"] == invoice["base_rate"] == 1000
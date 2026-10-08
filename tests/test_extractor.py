from pathlib import Path

from generate_mock_pdfs import generate_mock_pdfs
from src.extractor import parse_carrier_pdf, sync_inbound_pdfs_to_supabase


def test_parse_carrier_pdf_extracts_expected_fields(tmp_path):
    root = tmp_path / "sample_inbound_pdfs"
    generate_mock_pdfs(root)

    record = parse_carrier_pdf(str(root / "invoice_INV-1004.pdf"))

    assert record["invoice_number"] == "INV-1004"
    assert record["po_number"] == "PO-1004"
    assert record["carrier_name"] == "Northstar Freight"
    assert record["billed_base_freight"] == 1280.0
    assert record["billed_fuel_surcharge"] == 115.2
    assert record["billed_accessorial_fee"] == 150.0
    assert record["billed_weight_lbs"] == 6000.0
    assert record["invoice_date"] == "2026-09-04"


def test_generate_mock_pdfs_creates_expected_files(tmp_path):
    root = tmp_path / "sample_inbound_pdfs"
    generated = generate_mock_pdfs(root)

    assert (root / "invoice_INV-1004.pdf").exists()
    assert (root / "invoice_INV-1007.pdf").exists()
    assert set(generated) == {"invoice_INV-1004.pdf", "invoice_INV-1007.pdf"}


def test_sync_inbound_pdfs_upserts_using_invoice_id(tmp_path, monkeypatch):
    root = tmp_path / "inbound"
    generate_mock_pdfs(root)
    captured = {}

    class FakeTable:
        def upsert(self, records, on_conflict):
            captured["records"] = records
            captured["on_conflict"] = on_conflict
            return self

        def execute(self):
            return None

    class FakeClient:
        def table(self, name):
            captured["table"] = name
            return FakeTable()

    monkeypatch.setattr("src.extractor._create_supabase_client", lambda: FakeClient())
    summary = sync_inbound_pdfs_to_supabase(str(root))

    assert summary["processed_count"] == 2
    assert summary["failed_files"] == []
    assert captured["table"] == "carrier_invoices"
    assert captured["on_conflict"] == "invoice_id"
    inv_1004 = next(record for record in captured["records"] if record["invoice_id"] == "INV-1004")
    assert inv_1004 == {
        "invoice_id": "INV-1004",
        "invoice_number": "INV-1004",
        "carrier_name": "Northstar Freight",
        "issue_date": "2026-09-04",
        "invoice_date": "2026-09-04",
        "shipment_id": "PO-1004",
        "po_number": "PO-1004",
        "billed_weight": 6000.0,
        "billed_weight_lbs": 6000.0,
        "base_rate": 1280.0,
        "billed_base_freight": 1280.0,
        "fuel_surcharge": 115.2,
        "billed_fuel_surcharge": 115.2,
        "accessorial_fees": 150.0,
        "billed_accessorial_fee": 150.0,
    }

from pathlib import Path

from generate_mock_pdfs import generate_mock_pdfs
from src.extractor import parse_carrier_pdf, sync_inbound_pdfs_to_supabase


def test_parse_carrier_pdf_extracts_expected_fields(tmp_path):
    root = tmp_path / "sample_inbound_pdfs"
    generate_mock_pdfs(root)

    record = parse_carrier_pdf(str(root / "invoice_INV-1004.pdf"))

    assert record["invoice_number"] == "INV-1004"
    assert record["po_number"] == "PO-1004"
    assert record["bol_number"] == "BOL-1004"
    assert record["pro_number"] == "PRO-8841004"
    assert record["carrier_name"] == "Northstar Freight"
    assert record["billed_chargeable_weight"] == 6000.0
    assert record["billed_base_freight"] == 1280.0
    assert record["billed_fuel_surcharge"] == 115.2
    assert record["accessorial_items"] == [
        {"code": "LFT", "description": "Liftgate Delivery", "amount": 60.0},
        {"code": "DET", "description": "Driver Detention", "amount": 90.0},
    ]
    assert record["billed_accessorial_fee"] == 150.0
    assert record["invoice_total_due"] == 1545.2
    assert record["billed_weight_lbs"] == 6000.0
    assert record["invoice_date"] == "2026-09-04"
    assert record["bill_to_name"] == "ABC Company"
    assert "FICTIONAL DEMO" in record["bill_to_address"]
    assert record["remit_to_name"] == "Northstar Freight Remittance"
    assert "FICTIONAL" in record["remit_to_address"]
    assert record["payment_terms"] == "Net 30"
    assert record["payment_due_date"] == "2026-10-04"
    assert record["pickup_date"] == "2026-09-03"
    assert record["delivery_date"] == "2026-09-04"
    assert record["freight_class"] == "Class 70"
    assert record["nmfc_code"] == "156600-02"
    assert record["rate_tariff_reference"] == "NSF-DEMO-2026"
    assert record["rate_per_cwt"] == 21.3333
    assert record["handling_unit_count"] == 4
    assert record["handling_unit_type"] == "Pallets / PLT"


def test_mock_invoice_includes_bill_to_dispute_contact(tmp_path):
    import pdfplumber

    generate_mock_pdfs(tmp_path)

    with pdfplumber.open(tmp_path / "invoice_INV-1004.pdf") as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)

    assert "BILL TO / DISPUTE CONTACT" in text
    assert "ABC Company" in text
    assert "Nadia R." in text
    assert "Accounts Payable Manager" in text
    assert "Phone: 0412345678" in text


def test_generate_mock_pdfs_creates_expected_files(tmp_path):
    root = tmp_path / "sample_inbound_pdfs"
    generated = generate_mock_pdfs(root)

    assert (root / "invoice_INV-1004.pdf").exists()
    assert (root / "invoice_INV-1007.pdf").exists()
    assert (root / "invoice_INV-10013.pdf").exists()
    assert set(generated) == {
        "invoice_INV-1004.pdf",
        "invoice_INV-1007.pdf",
        "invoice_INV-10013.pdf",
    }


def test_mock_pdf_generator_can_select_existing_inbound_invoice_numbers(tmp_path):
    generated = generate_mock_pdfs(
        tmp_path, invoice_numbers={"INV-1004", "INV-10013"}
    )

    assert generated == {"invoice_INV-1004.pdf", "invoice_INV-10013.pdf"}
    assert len(list(tmp_path.glob("*.pdf"))) == 2


def test_parse_itemized_example_invoice_includes_total_and_bol(tmp_path):
    root = tmp_path / "sample_inbound_pdfs"
    generate_mock_pdfs(root)

    record = parse_carrier_pdf(root / "invoice_INV-1007.pdf")

    assert record["bol_number"] == "BOL-1007"
    assert record["pro_number"] == "PRO-8841007"
    assert record["billed_chargeable_weight"] == 15000.0
    assert record["accessorial_items"] == [
        {"code": "LFT", "description": "Liftgate Delivery", "amount": 90.0},
        {"code": "DET", "description": "Driver Detention", "amount": 90.0},
    ]
    assert record["invoice_total_due"] == 3645.0
    assert record["payment_due_date"] == "2026-10-07"
    assert record["handling_unit_count"] == 10


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

    assert summary["processed_count"] == 3
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
        "bol_number": "BOL-1004",
        "pro_number": "PRO-8841004",
        "billed_chargeable_weight": 6000.0,
        "billed_weight": 6000.0,
        "billed_weight_lbs": 6000.0,
        "invoice_total_due": 1545.2,
        "base_rate": 1280.0,
        "billed_base_freight": 1280.0,
        "fuel_surcharge": 115.2,
        "billed_fuel_surcharge": 115.2,
        "accessorial_items": [
            {"code": "LFT", "description": "Liftgate Delivery", "amount": 60.0},
            {"code": "DET", "description": "Driver Detention", "amount": 90.0},
        ],
        "accessorial_fees": 150.0,
        "billed_accessorial_fee": 150.0,
        "bill_to_name": "ABC Company",
        "bill_to_address": "100 Demo Way, Seattle, WA 98101 (FICTIONAL DEMO)",
        "remit_to_name": "Northstar Freight Remittance",
        "remit_to_address": "500 Carrier Demo Blvd, Denver, CO 80202 (FICTIONAL)",
        "payment_terms": "Net 30",
        "payment_due_date": "2026-10-04",
        "pickup_date": "2026-09-03",
        "delivery_date": "2026-09-04",
        "freight_class": "Class 70",
        "nmfc_code": "156600-02",
        "rate_tariff_reference": "NSF-DEMO-2026",
        "rate_per_cwt": 21.3333,
        "handling_unit_count": 4,
        "handling_unit_type": "Pallets / PLT",
    }

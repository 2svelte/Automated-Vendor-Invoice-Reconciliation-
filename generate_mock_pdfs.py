from __future__ import annotations

import json
import os
from datetime import date, timedelta
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "data" / "sample_inbound_pdfs"


def generate_mock_pdfs(
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    invoice_numbers: set[str] | None = None,
) -> set[str]:
    """Create sample carrier invoices with itemized accessorial and shipment details."""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    profile_path = Path(__file__).resolve().parent / "data" / "company_profile.json"
    with profile_path.open(encoding="utf-8") as profile_file:
        company_profile = json.load(profile_file)
    dispute_contact = company_profile["dispute_contact"]

    samples = [
        {
            "file": "invoice_INV-1004.pdf",
            "inv": "INV-1004",
            "po": "PO-1004",
            "carrier": "Northstar Freight",
            "date": "2026-09-04",
            "base": 1280.00,
            "fuel": 115.20,
            "accessorial_items": [
                ("LFT", "Liftgate Delivery", 60.00),
                ("DET", "Driver Detention", 90.00),
            ],
            "weight": 6000,
            "bol": "BOL-1004",
            "pro": "PRO-8841004",
            "origin": "Seattle, WA",
            "destination": "Portland, OR",
            "remit_to_name": "Northstar Freight Remittance",
            "remit_to_address": "500 Carrier Demo Blvd, Denver, CO 80202 (FICTIONAL)",
            "pickup_date": "2026-09-03",
            "delivery_date": "2026-09-04",
            "freight_class": "Class 70",
            "nmfc_code": "156600-02",
            "rate_tariff_reference": "NSF-DEMO-2026",
            "rate_per_cwt": 21.3333,
            "handling_unit_count": 4,
            "handling_unit_type": "Pallets / PLT",
        },
        {
            "file": "invoice_INV-1007.pdf",
            "inv": "INV-1007",
            "po": "PO-1007",
            "carrier": "Northstar Freight",
            "date": "2026-09-07",
            "base": 3180.00,
            "fuel": 285.00,
            "accessorial_items": [
                ("LFT", "Liftgate Delivery", 90.00),
                ("DET", "Driver Detention", 90.00),
            ],
            "weight": 15000,
            "bol": "BOL-1007",
            "pro": "PRO-8841007",
            "origin": "Denver, CO",
            "destination": "Salt Lake City, UT",
            "remit_to_name": "Northstar Freight Remittance",
            "remit_to_address": "500 Carrier Demo Blvd, Denver, CO 80202 (FICTIONAL)",
            "pickup_date": "2026-09-06",
            "delivery_date": "2026-09-07",
            "freight_class": "Class 70",
            "nmfc_code": "156600-02",
            "rate_tariff_reference": "NSF-DEMO-2026",
            "rate_per_cwt": 21.2,
            "handling_unit_count": 10,
            "handling_unit_type": "Pallets / PLT",
        },
        {
            "file": "invoice_INV-10013.pdf",
            "inv": "INV-10013",
            "po": "PO-10013",
            "carrier": "Northstar Freight",
            "date": "2026-09-07",
            "base": 3180.00,
            "fuel": 285.00,
            "accessorial_items": [
                ("LFT", "Liftgate Delivery", 90.00),
                ("DET", "Driver Detention", 90.00),
            ],
            "weight": 15000,
            "bol": "BOL-10013",
            "pro": "PRO-88410013",
            "origin": "Denver, CO",
            "destination": "Salt Lake City, UT",
            "remit_to_name": "Northstar Freight Remittance",
            "remit_to_address": "500 Carrier Demo Blvd, Denver, CO 80202 (FICTIONAL)",
            "pickup_date": "2026-09-06",
            "delivery_date": "2026-09-07",
            "freight_class": "Class 70",
            "nmfc_code": "156600-02",
            "rate_tariff_reference": "NSF-DEMO-2026",
            "rate_per_cwt": 21.2,
            "handling_unit_count": 10,
            "handling_unit_type": "Pallets / PLT",
        },
    ]

    generated: set[str] = set()
    for sample in samples:
        if invoice_numbers is not None and sample["inv"] not in invoice_numbers:
            continue
        file_name = sample["file"]
        pdf_path = output_path / file_name
        generated.add(file_name)

        pdf_canvas = canvas.Canvas(str(pdf_path), pagesize=letter)
        pdf_canvas.setFont("Helvetica-Bold", 16)
        pdf_canvas.drawString(50, 750, f"FREIGHT BILLING INVOICE: {sample['carrier']}")
        pdf_canvas.setFont("Helvetica-Bold", 10)
        pdf_canvas.drawString(50, 722, "BILL TO / DISPUTE CONTACT")
        pdf_canvas.setFont("Helvetica", 9)
        pdf_canvas.drawString(50, 706, f"Bill To Name: {company_profile['company_name']}")
        pdf_canvas.drawString(
            50, 691, f"Bill To Address: {company_profile['bill_to_address']}"
        )
        pdf_canvas.drawString(50, 676, f"AP Contact: {dispute_contact['name']}")
        pdf_canvas.drawString(50, 661, f"AP Contact Title: {dispute_contact['position']}")
        pdf_canvas.drawString(50, 646, f"AP Contact Phone: {dispute_contact['phone']}")
        pdf_canvas.drawString(50, 626, f"Remit To Name: {sample['remit_to_name']}")
        pdf_canvas.drawString(
            50, 611, f"Remit To Address: {sample['remit_to_address']}"
        )
        pdf_canvas.line(50, 597, 550, 597)
        pdf_canvas.setFont("Helvetica", 9)
        document_fields = [
            ("Invoice Number", sample["inv"]),
            ("Billing Date", sample["date"]),
            ("Associated PO", sample["po"]),
            ("Carrier PRO Number", sample["pro"]),
            ("Shipper BOL Number", sample["bol"]),
            ("Payment Terms", "Net 30"),
            ("Payment Due Date", (date.fromisoformat(sample["date"]) + timedelta(days=30)).isoformat()),
            ("Pickup Date", sample["pickup_date"]),
            ("Delivery Date", sample["delivery_date"]),
            ("Origin", sample["origin"]),
            ("Destination", sample["destination"]),
            ("Billed Chargeable Weight", f"{sample['weight']:,} lbs"),
            ("Freight Class", sample["freight_class"]),
            ("NMFC Code", sample["nmfc_code"]),
            ("Rate Tariff Reference", sample["rate_tariff_reference"]),
            ("Rate Per CWT", f"${sample['rate_per_cwt']:.4f}"),
            ("Handling Unit Count", str(sample["handling_unit_count"])),
            ("Handling Unit Type", sample["handling_unit_type"]),
        ]
        y_position = 580
        for label, value in document_fields:
            pdf_canvas.drawString(50, y_position, f"{label}: {value}")
            y_position -= 14
        pdf_canvas.line(50, y_position - 5, 550, y_position - 5)
        y_position -= 25
        pdf_canvas.drawString(50, y_position, f"Base Freight Linehaul: ${sample['base']:.2f}")
        y_position -= 20
        pdf_canvas.drawString(50, y_position, f"Fuel Surcharge: ${sample['fuel']:.2f}")
        y_position -= 20
        for code, description, amount in sample["accessorial_items"]:
            pdf_canvas.drawString(
                50,
                y_position,
                f"Accessorial Item: {code} | {description} | ${amount:.2f}",
            )
            y_position -= 20
        pdf_canvas.line(50, y_position - 5, 550, y_position - 5)

        total = sample["base"] + sample["fuel"] + sum(
            amount for _, _, amount in sample["accessorial_items"]
        )
        pdf_canvas.setFont("Helvetica-Bold", 11)
        pdf_canvas.drawString(50, y_position - 25, f"TOTAL CHARGES DUE: ${total:.2f}")
        pdf_canvas.save()

    return generated


if __name__ == "__main__":
    files = generate_mock_pdfs()
    print("Generated sample invoice PDFs:")
    for file_name in sorted(files):
        print(os.path.join("data", "sample_inbound_pdfs", file_name))

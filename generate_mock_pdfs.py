from __future__ import annotations

import os
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "data" / "sample_inbound_pdfs"


def generate_mock_pdfs(output_dir: str | Path = DEFAULT_OUTPUT_DIR) -> set[str]:
    """Create sample carrier invoices with itemized accessorial and shipment details."""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

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
        },
    ]

    generated: set[str] = set()
    for sample in samples:
        file_name = sample["file"]
        pdf_path = output_path / file_name
        generated.add(file_name)

        pdf_canvas = canvas.Canvas(str(pdf_path), pagesize=letter)
        pdf_canvas.setFont("Helvetica-Bold", 16)
        pdf_canvas.drawString(50, 750, f"FREIGHT BILLING INVOICE: {sample['carrier']}")

        pdf_canvas.setFont("Helvetica", 10)
        pdf_canvas.drawString(50, 720, f"Invoice Number: {sample['inv']}")
        pdf_canvas.drawString(50, 705, f"Associated PO: {sample['po']}")
        pdf_canvas.drawString(50, 690, f"Billing Date: {sample['date']}")
        pdf_canvas.drawString(50, 675, f"Carrier PRO Number: {sample['pro']}")
        pdf_canvas.drawString(50, 660, f"Shipper BOL Number: {sample['bol']}")
        pdf_canvas.drawString(50, 645, f"Origin: {sample['origin']}")
        pdf_canvas.drawString(50, 630, f"Destination: {sample['destination']}")
        pdf_canvas.drawString(50, 615, "Payment Terms: Net 30")
        pdf_canvas.drawString(50, 600, f"Billed Chargeable Weight: {sample['weight']:,} lbs")

        pdf_canvas.line(50, 585, 550, 585)
        pdf_canvas.drawString(50, 565, f"Base Freight Linehaul: ${sample['base']:.2f}")
        pdf_canvas.drawString(50, 545, f"Fuel Surcharge: ${sample['fuel']:.2f}")
        y_position = 525
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

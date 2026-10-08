from __future__ import annotations

import os
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "data" / "sample_inbound_pdfs"


def generate_mock_pdfs(output_dir: str | Path = DEFAULT_OUTPUT_DIR) -> set[str]:
    """Create realistic sample invoice PDFs that match the project's current invoice schema."""
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
            "accessorial": 150.00,
            "weight": 6000,
        },
        {
            "file": "invoice_INV-1007.pdf",
            "inv": "INV-1007",
            "po": "PO-1007",
            "carrier": "Northstar Freight",
            "date": "2026-09-07",
            "base": 3180.00,
            "fuel": 285.00,
            "accessorial": 180.00,
            "weight": 15000,
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
        pdf_canvas.drawString(50, 675, f"Certified Scale Weight: {sample['weight']} lbs")

        pdf_canvas.line(50, 660, 550, 660)
        pdf_canvas.drawString(50, 640, f"Base Freight Linehaul: ${sample['base']:.2f}")
        pdf_canvas.drawString(50, 620, f"Fuel Surcharge Index:  ${sample['fuel']:.2f}")
        pdf_canvas.drawString(50, 600, f"Accessorial Surcharges: ${sample['accessorial']:.2f}")
        pdf_canvas.line(50, 580, 550, 580)

        total = sample["base"] + sample["fuel"] + sample["accessorial"]
        pdf_canvas.setFont("Helvetica-Bold", 11)
        pdf_canvas.drawString(50, 560, f"TOTAL CHARGES DUE: ${total:.2f}")
        pdf_canvas.save()

    return generated


if __name__ == "__main__":
    files = generate_mock_pdfs()
    print("Generated sample invoice PDFs:")
    for file_name in sorted(files):
        print(os.path.join("data", "sample_inbound_pdfs", file_name))

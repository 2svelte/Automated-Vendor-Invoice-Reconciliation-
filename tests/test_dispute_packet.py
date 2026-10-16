from io import BytesIO

import pdfplumber

from src.dispute_packet import generate_dispute_packet_pdf


def test_evidence_packet_is_one_page_and_contains_dispute_and_source_evidence():
    invoice = {
        "invoice_number": "INV-1007",
        "po_number": "PO-1007",
        "carrier_name": "Northstar Freight",
        "total_invoiced": 3645.0,
        "disputed_amount": 557.5,
        "approved_remittance": 3087.5,
        "base_variance": 430.0,
        "fuel_variance": 37.5,
        "accessorial_variance": 90.0,
        "unauthorized_accessorial_items": [
            {"description": "Driver Detention", "overage": 90.0}
        ],
        "bol_number": "BOL-1007",
        "scale_ticket_id": "ST-1007",
        "certified_net_scale_weight": 42000,
        "gross_weight_lbs": 68000,
        "tare_weight_lbs": 26000,
        "scale_certification_seal": "NIST-SEAL-1007",
        "receiving_dock_door": "Bay 4",
        "received_timestamp": "2026-10-01T14:30:00Z",
        "dock_signature": "Receiver A",
        "osd_notes": "Two cartons crushed",
    }

    packet = generate_dispute_packet_pdf(
        invoice,
        "We have approved and queued a remittance of $3,087.50 for payment under "
        "PO-1007. The remaining $557.50 has been short-paid due to driver detention.",
    )

    assert packet.startswith(b"%PDF-")
    with pdfplumber.open(BytesIO(packet)) as pdf:
        assert len(pdf.pages) == 1
        text = pdf.pages[0].extract_text() or ""
    for evidence in (
        "DISPUTE SUMMARY VOUCHER",
        "$3,645.00",
        "$557.50",
        "$3,087.50",
        "ST-1007",
        "42,000 lb",
        "68,000 lb / 26,000 lb",
        "NIST-SEAL-1007",
        "Bay 4",
        "Receiver A",
        "Two cartons crushed",
    ):
        assert evidence in text

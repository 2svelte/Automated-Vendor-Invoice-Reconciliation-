"""Generate one-page, evidence-backed carrier dispute vouchers."""

from __future__ import annotations

from collections.abc import Mapping
from io import BytesIO
import math
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas
from reportlab.platypus import KeepInFrame, Paragraph, Spacer, Table, TableStyle


def _text(value: object, fallback: str = "Not provided") -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    return text if text and text.lower() not in {"nan", "none", "<na>"} else fallback


def _number(value: object) -> float | None:
    try:
        number = float(str(value))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _money(value: object) -> str:
    number = _number(value)
    return f"${number:,.2f}" if number is not None else "N/A"


def _weight(value: object) -> str:
    number = _number(value)
    return f"{number:,.0f} lb" if number is not None else _text(value)


def _paragraph(value: object, style: ParagraphStyle) -> Paragraph:
    text = escape(_text(value)).replace("\n", "<br/>")
    return Paragraph(text, style)


def _line_items(invoice: Mapping[str, object]) -> list[list[object]]:
    rows: list[list[object]] = [["Disputed line item", "Line-item variance"]]
    for label, key in (
        ("Base rate variance", "base_variance"),
        ("Fuel surcharge variance", "fuel_variance"),
    ):
        value = _number(invoice.get(key)) or 0
        if value != 0:
            rows.append([label, _money(value) if value > 0 else f"-{_money(abs(value))}"])

    items = invoice.get("unauthorized_accessorial_items")
    if isinstance(items, list) and items:
        for item in items:
            if not isinstance(item, Mapping):
                continue
            overage = _number(item.get("overage")) or 0
            if overage > 0:
                description = item.get("description") or item.get("code") or "Accessorial"
                rows.append([f"Unauthorized {description}", _money(overage)])
    else:
        accessorial_variance = _number(invoice.get("accessorial_variance")) or 0
        if accessorial_variance > 0:
            rows.append(["Accessorial variance", _money(accessorial_variance)])
    if len(rows) == 1:
        rows.append(["No positive line-item variance recorded", "$0.00"])
    return rows


def generate_dispute_packet_pdf(
    invoice: Mapping[str, object],
    memo: str,
) -> bytes:
    """Build a compact, single-page PDF with memo, variances, and source evidence."""
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "VoucherTitle",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=15,
        leading=17,
        alignment=1,
        spaceAfter=4,
    )
    section_style = ParagraphStyle(
        "VoucherSection",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=8.5,
        leading=10,
        textColor=colors.HexColor("#16324f"),
        spaceBefore=3,
        spaceAfter=2,
    )
    body_style = ParagraphStyle(
        "VoucherBody",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=7.5,
        leading=9,
        spaceAfter=1,
    )
    label_style = ParagraphStyle(
        "VoucherLabel",
        parent=body_style,
        fontName="Helvetica-Bold",
    )

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    pdf.setTitle(f"Dispute Summary Voucher - {_text(invoice.get('invoice_number'))}")

    financial_rows = [
        ["Total invoiced", "Withheld / disputed", "Net approved remittance"],
        [
            _money(invoice.get("total_invoiced")),
            f"-{_money(invoice.get('disputed_amount', invoice.get('total_overbill')))}",
            _money(invoice.get("approved_remittance")),
        ],
    ]
    financial_table = Table(
        financial_rows,
        colWidths=[2.15 * inch, 2.15 * inch, 2.15 * inch],
    )
    financial_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eef5")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#16324f")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#9aa9b8")),
                ("INNERGRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#c4ced8")),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )

    line_items = Table(_line_items(invoice), colWidths=[4.9 * inch, 1.55 * inch])
    line_items.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eef5")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 7.5),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#c4ced8")),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )

    scale_weight = invoice.get(
        "certified_net_scale_weight", invoice.get("actual_scale_weight_lbs")
    )
    receiving_timestamp = invoice.get(
        "received_timestamp", invoice.get("delivery_timestamp")
    )
    metadata_rows = [
        [
            _paragraph("Invoice / PO", label_style),
            _paragraph(
                f"{_text(invoice.get('invoice_number'))} / {_text(invoice.get('po_number'))}",
                body_style,
            ),
            _paragraph("Carrier / BOL", label_style),
            _paragraph(
                f"{_text(invoice.get('carrier_name'))} / {_text(invoice.get('bol_number'))}",
                body_style,
            ),
        ],
        [
            _paragraph("Scale ticket ID", label_style),
            _paragraph(invoice.get("scale_ticket_id"), body_style),
            _paragraph("Certified net weight", label_style),
            _paragraph(_weight(scale_weight), body_style),
        ],
        [
            _paragraph("Gross / tare weight", label_style),
            _paragraph(
                f"{_weight(invoice.get('gross_weight_lbs'))} / "
                f"{_weight(invoice.get('tare_weight_lbs'))}",
                body_style,
            ),
            _paragraph("NIST certification seal", label_style),
            _paragraph(invoice.get("scale_certification_seal"), body_style),
        ],
        [
            _paragraph("Receiving bay door", label_style),
            _paragraph(invoice.get("receiving_dock_door"), body_style),
            _paragraph("Receiving timestamp", label_style),
            _paragraph(receiving_timestamp, body_style),
        ],
        [
            _paragraph("Receiver signature", label_style),
            _paragraph(invoice.get("dock_signature"), body_style),
            _paragraph("OS&D damage notes", label_style),
            _paragraph(invoice.get("osd_notes"), body_style),
        ],
    ]
    evidence_table = Table(
        metadata_rows,
        colWidths=[1.2 * inch, 2.0 * inch, 1.35 * inch, 1.9 * inch],
    )
    evidence_table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#c4ced8")),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f2f5f8")),
                ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#f2f5f8")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )

    story = [
        Paragraph("DISPUTE SUMMARY VOUCHER", title_style),
        Paragraph(
            f"Invoice {_text(invoice.get('invoice_number'))}  |  "
            f"PO {_text(invoice.get('po_number'))}  |  "
            f"{_text(invoice.get('carrier_name'))}",
            body_style,
        ),
        Spacer(1, 4),
        financial_table,
        Paragraph("Formal Dispute Memo", section_style),
        _paragraph(memo, body_style),
        Paragraph("Line-item variances", section_style),
        line_items,
        Paragraph("Certified scale ticket and receiving evidence", section_style),
        evidence_table,
        Spacer(1, 3),
        Paragraph(
            "Compiled from the invoice reconciliation and warehouse receipt records.",
            ParagraphStyle("Footer", parent=body_style, fontSize=6.5, textColor=colors.grey),
        ),
    ]
    fit_story = KeepInFrame(
        letter[0] - 0.9 * inch,
        letter[1] - 0.75 * inch,
        story,
        mode="shrink",
        hAlign="LEFT",
        vAlign="TOP",
    )
    fit_story.wrapOn(pdf, letter[0] - 0.9 * inch, letter[1] - 0.75 * inch)
    fit_story.drawOn(pdf, 0.45 * inch, 0.375 * inch)
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()

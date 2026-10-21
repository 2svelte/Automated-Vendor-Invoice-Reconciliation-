import pytest

import pandas as pd

from app import (
    _comparison_table,
    _default_carrier_email,
    _display_status,
    _dispute_memo_button_label,
    _format_invoice_accuracy,
    _format_withheld_amount,
    _leakage_chart_html,
    _dispute_packet_filename,
    _format_billing_variance,
    _primary_root_cause,
    _rebuttal_sla,
    _table_component_height,
    _workbench_table_html,
    _weight_delta,
    format_weight,
)
from src.reconciliation_engine import UNAUTHORIZED_FUEL_CREEP


def test_default_carrier_email_prefers_saved_recipient_then_carrier_record() -> None:
    invoice = pd.Series(
        {
            "carrier_name": "Northstar Freight",
            "carrier_email": "carrier@example.com",
            "dispute_to_email": "",
        }
    )
    assert _default_carrier_email(invoice) == "carrier@example.com"

    invoice["dispute_to_email"] = "saved@example.com"
    assert _default_carrier_email(invoice) == "saved@example.com"


def test_dispute_memo_button_label_tracks_current_session_generation() -> None:
    assert _dispute_memo_button_label(False) == "Generate Dispute Memo"
    assert _dispute_memo_button_label(True) == "Regenerate Dispute Memo"


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("DISCREPANCY_FLAGGED", "Discrepancy Flagged"),
        ("REVIEW_INCOMPLETE", "Review Incomplete"),
        ("DUPLICATE_BILLING_BLOCKED", "Duplicate Billing Blocked"),
        ("REBUTTAL_RECEIVED", "Rebuttal Received"),
        ("custom_status_flag", "Custom Status Flag"),
    ],
)
def test_display_status_uses_readable_title_case(
    status: str, expected: str
) -> None:
    assert _display_status(status) == expected


def test_table_component_height_keeps_spacing_compact() -> None:
    assert _table_component_height(1) == 96
    assert _table_component_height(6) == 230


def test_rebuttal_sla_and_evidence_filename_formatting() -> None:
    deadline = pd.Timestamp.now(tz="UTC") + pd.Timedelta(days=28)

    assert _rebuttal_sla(deadline) == "28 days remaining"
    assert _rebuttal_sla(deadline, "REBUTTAL_RECEIVED") == "Response received"
    assert _rebuttal_sla(deadline, "CLOSED_RESOLVED") == "Closed"
    assert _rebuttal_sla("invalid deadline") == "—"
    assert _dispute_packet_filename("INV-1007 / duplicate") == (
        "Dispute_Packet_INV-1007duplicate.pdf"
    )


def test_duplicate_invoice_is_highlighted_in_queue() -> None:
    duplicate = pd.Series(
        {
            "duplicate_of_invoice_id": "INV-1007",
            "receipt_found": True,
        }
    )
    table = pd.DataFrame(
        {
            "Status": ["DUPLICATE_BILLING_BLOCKED"],
            "Primary Root Cause": [_primary_root_cause(duplicate)],
        }
    )

    html = _workbench_table_html(
        table,
        table_class="worklist-table",
        column_widths=[40, 60],
    )

    assert _primary_root_cause(duplicate) == (
        "Duplicate bill — previously processed INV-1007"
    )
    assert 'class="status-cell"' in html
    assert 'class="root-cause-cell"' in html
    assert "status-duplicate" not in html
    assert "Duplicate Match" not in html
    assert "duplicate-match" not in html
    assert html.count('class="column-resizer"') == 1
    assert 'data-table-key="worklist-table"' in html
    assert "pointerdown" in html
    assert "localStorage.setItem(storageKey" in html


def test_discrepancy_status_does_not_highlight_total() -> None:
    table = pd.DataFrame(
        {
            "Status": ["Discrepancy Flagged"],
            "Total": ["$125.00"],
        }
    )

    html = _workbench_table_html(
        table,
        table_class="worklist-table",
        column_widths=[50, 50],
    )

    assert 'class="status-cell"' in html
    assert "status-flagged" not in html
    assert '<td title="$125.00">$125.00</td>' in html
    assert "discrepancy-flagged" not in html
    assert (
        "table.workbench-table .status-cell,\n"
        "            table.workbench-table .root-cause-cell {"
    ) in html
    assert "font-family: inherit; font-size: 16px;" in html
    assert "font-weight: 400;" in html
    assert "table.workbench-table th {\n              position: relative; padding-right: 12px; background: #15191f;\n              color: #f1f3f5; font-weight: 600;" in html
    assert "table.workbench-table .status-cell { text-align: left; }" in html
    assert "receipt-missing" not in html


def test_receipt_does_not_highlight_missing() -> None:
    table = pd.DataFrame({"Receipt": ["Found", "Missing"]})

    html = _workbench_table_html(
        table,
        table_class="worklist-table",
        column_widths=[50],
    )

    assert '<td title="Found">Found</td>' in html
    assert '<td title="Missing">Missing</td>' in html
    assert "receipt-found" not in html
    assert "receipt-missing" not in html


def test_grouped_header_spans_discrepancy_columns() -> None:
    table = pd.DataFrame(
        {
            "Status": ["Review Required"],
            "Base": ["+$5.00"],
            "Fuel": ["$0.00"],
            "Total": ["$5.00"],
            "Receipt": ["Found"],
        }
    )

    html = _workbench_table_html(
        table,
        table_class="worklist-table",
        column_widths=[20, 20, 20, 20, 20],
        column_groups={"Discrepancies": ["Base", "Fuel", "Total"]},
    )

    assert '<th scope="colgroup" colspan="3" class="column-group">Discrepancies</th>' in html
    assert '<th scope="col" rowspan="2">Status' in html
    assert '<th scope="col">Base' in html
    assert "Δ" not in html
    assert 'data-column-index="3"' in html
    assert _table_component_height(2, header_rows=2) == 134


@pytest.mark.parametrize(
    ("accuracy", "clean_invoices", "invoice_count", "expected"),
    [
        (0.2, 1, 5, "20% (1 / 5 Clean)"),
        (0.75, 3, 4, "75% (3 / 4 Clean)"),
        (1.0, 4, 4, "100% (4 / 4 Clean)"),
    ],
)
def test_format_invoice_accuracy(
    accuracy: float,
    clean_invoices: int,
    invoice_count: int,
    expected: str,
) -> None:
    assert (
        _format_invoice_accuracy(accuracy, clean_invoices, invoice_count)
        == expected
    )


@pytest.mark.parametrize(
    ("billed", "authorized", "expected"),
    [
        (95, 100, "-$5.00"),
        (105, 100, "+$5.00"),
        (100, 100, "$0.00"),
        (None, 100, "N/A"),
    ],
)
def test_format_billing_variance(
    billed: object, authorized: object, expected: str
) -> None:
    assert _format_billing_variance(billed, authorized) == expected


def test_withheld_amount_is_positive_aud() -> None:
    assert _format_withheld_amount(12.5) == "$12.50 AUD"
    assert _format_withheld_amount(0) == "$0.00 AUD"


def test_reconciliation_table_uses_aip_fuel_authorization_and_metric_weights() -> None:
    invoice = pd.Series(
        {
            "contracted_base_freight": 2750,
            "billed_base_freight": 3180,
            "billed_fuel_surcharge": 285,
            "fuel_shipment_date": "2026-09-07",
            "billed_weight_lbs": 15000,
            "contracted_expected_weight": 14000,
            "actual_scale_weight_lbs": 14000,
            "gross_weight_lbs": 35000,
            "tare_weight_lbs": 21000,
        }
    )

    table = _comparison_table(invoice)
    table_html = _workbench_table_html(
        table,
        table_class="reconciliation-table",
        column_widths=[29, 18, 18, 18, 17],
    )

    assert table.loc[0, "Component"] == "Financial Line Items (Charges)"
    assert table.loc[2, "Component"] == "Fuel Surcharge (AIP Weekly Index)"
    assert table.loc[2, "Authorized (PO)"] == "$123.75 AUD"
    assert table.loc[1, "Variance"] == "+$430.00"
    assert table.loc[2, "Variance"] == "+$161.25"
    assert table.loc[3, "Component"] == "Accessorials"
    assert table.loc[5, "Component"] == (
        "Physical Scale / Weight Verification "
        "(Compliance & Billing Basis)"
    )
    assert table.loc[6, "Component"] == "Certified Net Chargeable Weight"
    assert table.loc[7, "Component"] == "\u00a0\u00a0• Gross Vehicle Mass (GVM)"
    assert table.loc[8, "Component"] == (
        "\u00a0\u00a0• Empty Truck + Trailer Tare Weight"
    )
    assert "6,804 kg" in table.loc[6].to_list()
    assert "lb" not in " ".join(table.astype(str).to_numpy().flatten())
    assert table_html.count('class="column-resizer"') == 4
    assert table_html.count('class="table-tier-heading"') == 2
    assert 'data-table-key="reconciliation-table"' in table_html
    assert 'class="variance-flagged"' in table_html
    assert 'class="variance-overcharge-badge"' in table_html
    assert 'class="variance-fuel-overcharge"' in table_html
    assert (
        'class="variance-fuel-overcharge" title="+$161.25">+$161.25</td>'
        in table_html
    )
    assert "Overbill" not in table_html
    assert format_weight(15000) == "6,804 kg"
    assert _weight_delta(15000) == "+6,804 kg"


def test_reconciliation_table_identifies_base_underbill_as_credit() -> None:
    invoice = pd.Series(
        {
            "contracted_base_freight": 100,
            "billed_base_freight": 95,
            "billed_fuel_surcharge": 0,
            "authorized_fuel_surcharge": 0,
            "billed_accessorial_fee": 0,
            "max_accessorial_allowance": 0,
        }
    )

    table = _comparison_table(invoice)
    table_html = _workbench_table_html(
        table,
        table_class="reconciliation-table",
        column_widths=[29, 18, 18, 18, 17],
    )

    assert table.loc[1, "Variance"] == "-$5.00"
    assert 'class="variance-credit"' in table_html
    assert "Underbill" not in table_html


def test_reconciliation_table_marks_itemized_accessorial_savings_favorably() -> None:
    invoice = pd.Series(
        {
            "accessorial_items": [
                {"code": "LFT", "description": "Liftgate", "amount": 95}
            ],
            "approved_accessorial_items": [
                {"code": "LFT", "description": "Liftgate", "amount": 100}
            ],
        }
    )
    table = _comparison_table(invoice)
    assert table.loc[4, "Component"] == "\u00a0\u00a0• LFT: Liftgate"
    table_html = _workbench_table_html(
        table,
        table_class="reconciliation-table",
        column_widths=[29, 18, 18, 18, 17],
    )

    assert table.loc[4, "Variance"] == "-$5.00"
    assert 'class="variance-credit"' in table_html
    assert 'class="variance-overcharge-badge">' not in table_html


def test_fuel_overcharge_is_primary_exception_root_cause() -> None:
    invoice = pd.Series(
        {
            "root_cause": UNAUTHORIZED_FUEL_CREEP,
            "receipt_found": True,
        }
    )

    assert _primary_root_cause(invoice) == "Unauthorized Fuel Creep"


def test_primary_root_cause_formats_accessorial_descriptions_without_codes() -> None:
    invoice = pd.Series(
        {
            "receipt_found": True,
            "unauthorized_accessorial_items": [
                {"code": "LFT", "description": "Liftgate Delivery"},
                {"code": "DET", "description": "Driver Detention"},
            ],
        }
    )

    assert _primary_root_cause(invoice) == (
        "Unapproved Accessorials: Driver Detention, Liftgate Delivery"
    )


def test_leakage_chart_renders_amounts_and_shares_without_html_injection() -> None:
    summary = pd.DataFrame(
        {
            "Leakage category": ["Base rate inflation", "<script>alert(1)</script>"],
            "Amount": [455.0, 190.0],
            "Share": [0.641, 0.268],
        }
    )

    chart = _leakage_chart_html(summary)

    assert "$455.00 (64.1%)" in chart
    assert "$190.00 (26.8%)" in chart
    assert chart.count("color:#fff") == 4
    assert "left:calc(55.00% + 0.5rem)" in chart
    assert "background:#f1f5f9" not in chart
    assert "background:#1e3a5f" not in chart
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in chart
    assert "<script>alert(1)</script>" not in chart

import pytest

import pandas as pd

from app import (
    _comparison_table,
    _default_carrier_email,
    _dispute_memo_button_label,
    _format_invoice_accuracy,
    _leakage_chart_html,
    _dispute_packet_filename,
    _primary_root_cause,
    _rebuttal_sla,
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


def test_rebuttal_sla_and_evidence_filename_formatting() -> None:
    deadline = pd.Timestamp.now(tz="UTC") + pd.Timedelta(days=28)

    assert _rebuttal_sla(deadline) == "28 days remaining"
    assert _rebuttal_sla(deadline, "REBUTTAL_RECEIVED") == "Response received"
    assert _rebuttal_sla(deadline, "CLOSED_RESOLVED") == "Closed"
    assert _rebuttal_sla("invalid deadline") == "—"
    assert _dispute_packet_filename("INV-1007 / duplicate") == (
        "Dispute_Packet_INV-1007duplicate.pdf"
    )


def test_duplicate_invoice_is_highlighted_with_original_id_in_queue() -> None:
    duplicate = pd.Series(
        {
            "duplicate_of_invoice_id": "INV-1007",
            "receipt_found": True,
        }
    )
    table = pd.DataFrame(
        {
            "Status": ["DUPLICATE_BILLING_BLOCKED"],
            "Duplicate Match": ["Duplicate bill of previously processed INV-1007"],
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
    assert 'class="status-cell status-duplicate"' in html
    assert "Duplicate bill of previously processed INV-1007" in html
    assert 'class="discrepancy-flagged duplicate-match"' in html


@pytest.mark.parametrize(
    ("accuracy", "clean_invoices", "invoice_count", "expected"),
    [
        (0.2, 1, 5, "🔴 20% (1 / 5 Clean)"),
        (0.75, 3, 4, "🟡 75% (3 / 4 Clean)"),
        (1.0, 4, 4, "🟢 100% (4 / 4 Clean)"),
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


def test_reconciliation_table_uses_aip_fuel_authorization_and_metric_weights() -> None:
    invoice = pd.Series(
        {
            "contracted_base_freight": 2750,
            "billed_base_freight": 3180,
            "billed_fuel_surcharge": 285,
            "fuel_variance": -161.25,
            "fuel_shipment_date": "2026-09-07",
            "billed_weight_lbs": 15000,
            "contracted_expected_weight": 14000,
            "actual_scale_weight_lbs": 14000,
            "gross_weight_lbs": 35000,
            "tare_weight_lbs": 21000,
        }
    )

    table = _comparison_table(invoice)

    assert table.loc[1, "Component"] == "Fuel Surcharge (AIP Weekly Index)"
    assert table.loc[1, "Authorized (PO)"] == "$123.75 AUD"
    assert table.loc[1, "Variance"] == "-$161.25"
    assert table.loc[2, "Physical (WMS)"] == "—"
    assert "6,804 kg" in table.loc[5].to_list()
    assert "lb" not in " ".join(table.astype(str).to_numpy().flatten())
    assert format_weight(15000) == "6,804 kg"
    assert _weight_delta(15000) == "+6,804 kg"


def test_fuel_overcharge_is_primary_exception_root_cause() -> None:
    invoice = pd.Series(
        {
            "root_cause": UNAUTHORIZED_FUEL_CREEP,
            "receipt_found": True,
        }
    )

    assert _primary_root_cause(invoice) == UNAUTHORIZED_FUEL_CREEP


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

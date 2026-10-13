import pytest

import pandas as pd

from app import (
    _dispute_memo_button_label,
    _format_invoice_accuracy,
    _leakage_chart_html,
)


def test_dispute_memo_button_label_tracks_current_session_generation() -> None:
    assert _dispute_memo_button_label(False) == "Generate dispute memo"
    assert _dispute_memo_button_label(True) == "Regenerate dispute memo"


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

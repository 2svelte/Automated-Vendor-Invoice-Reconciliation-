import pandas as pd
import pytest

from src.reconciliation_engine import (
    DISCREPANCY_FLAGGED,
    REVIEW_INCOMPLETE,
    TOUCHLESS_APPROVED,
    reconcile,
    summarize,
    write_discrepancy_queue,
)


def sources(receipt_weight=1000, billed_weight=1000, receipt=True):
    po = pd.DataFrame([{
        "po_number": "PO-1", "vendor_id": "V-1", "carrier_name": "Carrier",
        "origin": "A", "destination": "B", "contracted_base_freight": 1000,
        "agreed_fuel_surcharge": 100, "max_accessorial_allowance": 50,
    }])
    invoice = pd.DataFrame([{
        "invoice_number": "INV-1", "po_number": "PO-1", "carrier_name": "Carrier",
        "billed_base_freight": 1000, "billed_fuel_surcharge": 100,
        "billed_accessorial_fee": 50, "billed_weight_lbs": billed_weight,
        "invoice_date": "2026-09-01",
    }])
    receipts = pd.DataFrame([{
        "po_number": "PO-1", "delivery_timestamp": "2026-09-01 10:00",
        "dock_signature": "Receiver", "actual_scale_weight_lbs": receipt_weight,
    }]) if receipt else pd.DataFrame(columns=["po_number", "delivery_timestamp", "dock_signature", "actual_scale_weight_lbs"])
    return po, invoice, receipts


def test_clean_three_way_match_is_touchless():
    result = reconcile(*sources())
    assert result.loc[0, "status"] == TOUCHLESS_APPROVED
    assert result.loc[0, "total_overbill"] == 0


def test_weight_tolerance_is_two_percent():
    within = reconcile(*sources(receipt_weight=1000, billed_weight=1020))
    over = reconcile(*sources(receipt_weight=1000, billed_weight=1021))
    assert within.loc[0, "status"] == TOUCHLESS_APPROVED
    assert over.loc[0, "status"] == DISCREPANCY_FLAGGED
    assert over.loc[0, "weight_variance_lbs"] == pytest.approx(1)


def test_missing_receipt_is_incomplete():
    result = reconcile(*sources(receipt=False))
    assert result.loc[0, "status"] == REVIEW_INCOMPLETE
    assert result.loc[0, "receipt_found"] == False


def test_overbill_math_and_flag_threshold():
    po, invoice, receipts = sources()
    invoice.loc[0, "billed_fuel_surcharge"] = 180
    invoice.loc[0, "billed_accessorial_fee"] = 100
    result = reconcile(po, invoice, receipts)
    assert result.loc[0, "fuel_variance"] == 80
    assert result.loc[0, "accessorial_variance"] == 50
    assert result.loc[0, "total_overbill"] == 130
    assert result.loc[0, "status"] == DISCREPANCY_FLAGGED


def test_summary_calculates_stp_and_prevented_spend():
    clean = reconcile(*sources())
    po, invoice, receipts = sources()
    invoice.loc[0, "billed_accessorial_fee"] = 100
    flagged = reconcile(po, invoice, receipts)
    metrics = summarize(pd.concat([clean, flagged], ignore_index=True))
    assert metrics["invoice_count"] == 2
    assert metrics["touchless_count"] == 1
    assert metrics["stp_rate"] == 0.5
    assert metrics["prevented_overbill"] == 50


def test_discrepancy_queue_writes_power_automate_contract(tmp_path):
    po, invoice, receipts = sources()
    invoice.loc[0, "billed_accessorial_fee"] = 130
    result = reconcile(po, invoice, receipts)
    queue = write_discrepancy_queue(result, tmp_path / "automation" / "discrepancy_queue.json")
    assert queue[0]["invoice_number"] == "INV-1"
    assert queue[0]["actions"] == ["approve_overcharge", "send_ai_dispute"]
    assert (tmp_path / "automation" / "discrepancy_queue.json").exists()


def test_incomplete_exception_queue_is_valid_json(tmp_path):
    result = reconcile(*sources(receipt=False))
    queue = write_discrepancy_queue(result, tmp_path / "discrepancy_queue.json")
    assert queue[0]["status"] == "REVIEW_INCOMPLETE"
    assert queue[0]["receipt_found"] is False

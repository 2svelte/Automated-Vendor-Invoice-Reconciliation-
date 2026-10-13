import pandas as pd
import pytest

from src.reconciliation_engine import (
    DISCREPANCY_FLAGGED,
    REVIEW_INCOMPLETE,
    TOUCHLESS_APPROVED,
    reconcile,
    summarize_carrier_billing,
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


def test_configured_fuel_tolerance_changes_status_not_audited_variance():
    po, invoice, receipts = sources()
    invoice["billed_fuel_surcharge"] = 100.5

    result = reconcile(po, invoice, receipts, fuel_tolerance=1.0)

    assert result.loc[0, "fuel_variance"] == pytest.approx(0.5)
    assert result.loc[0, "total_overbill"] == pytest.approx(0.5)
    assert result.loc[0, "status"] == TOUCHLESS_APPROVED


def test_configured_weight_buffer_controls_flag_threshold():
    po, invoice, receipts = sources(receipt_weight=1000, billed_weight=1030)

    result = reconcile(
        po,
        invoice,
        receipts,
        weight_tolerance_pct=3.0,
    )

    assert result.loc[0, "weight_variance_lbs"] == pytest.approx(0)
    assert result.loc[0, "status"] == TOUCHLESS_APPROVED


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


def test_itemized_accessorials_flag_unapproved_code_and_amount(tmp_path):
    po, invoice, receipts = sources()
    po["approved_accessorial_items"] = [[
        {"code": "LFT", "description": "Liftgate Delivery", "amount": 90}
    ]]
    invoice["accessorial_items"] = [[
        {"code": "LFT", "description": "Liftgate Delivery", "amount": 90},
        {"code": "DET", "description": "Driver Detention", "amount": 90},
    ]]
    invoice["billed_accessorial_fee"] = 180

    result = reconcile(po, invoice, receipts)

    assert result.loc[0, "accessorial_variance"] == 90
    assert result.loc[0, "unauthorized_accessorial_items"] == [{
        "code": "DET",
        "description": "Driver Detention",
        "billed_amount": 90.0,
        "authorized_amount": 0.0,
        "overage": 90.0,
    }]
    assert result.loc[0, "status"] == DISCREPANCY_FLAGGED
    queue = write_discrepancy_queue(result, tmp_path / "queue.json")
    assert queue[0]["unauthorized_accessorial_items"][0]["code"] == "DET"


def test_canonical_document_weight_fields_are_accepted():
    po, invoice, receipts = sources()
    po["authorized_carrier"] = po.pop("carrier_name")
    po["contracted_base_rate"] = po.pop("contracted_base_freight")
    po["contracted_fuel_surcharge"] = po.pop("agreed_fuel_surcharge")
    po["approved_accessorial_allowance"] = po.pop("max_accessorial_allowance")
    invoice["billed_chargeable_weight"] = invoice.pop("billed_weight_lbs")
    receipts["certified_net_scale_weight"] = receipts.pop("actual_scale_weight_lbs")
    receipts["received_timestamp"] = receipts.pop("delivery_timestamp")

    result = reconcile(po, invoice, receipts)

    assert result.loc[0, "weight_check_passed"]
    assert result.loc[0, "status"] == TOUCHLESS_APPROVED


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


def test_carrier_billing_summary_uses_reconciled_variances_and_nets_offsets():
    audited = pd.DataFrame(
        [
            {
                "carrier_name": "Northstar",
                "base_variance": 0,
                "fuel_variance": 0,
                "accessorial_variance": 0,
                "total_overbill": 0,
            },
            {
                "carrier_name": "Northstar",
                "base_variance": 60,
                "fuel_variance": 10,
                "accessorial_variance": 0,
                "total_overbill": 70,
            },
            {
                "carrier_name": "Atlas",
                "base_variance": 10,
                "fuel_variance": -5,
                "accessorial_variance": 5,
                "total_overbill": 10,
            },
        ]
    )

    carrier_summary, leakage_summary = summarize_carrier_billing(audited)

    northstar = carrier_summary.set_index("Carrier").loc["Northstar"]
    assert northstar["Invoices"] == 2
    assert northstar["Clean invoices"] == 1
    assert northstar["Accuracy index"] == pytest.approx(0.5)
    assert northstar["Primary leakage driver"] == "Base rate inflation"
    assert leakage_summary["Amount"].sum() == pytest.approx(80)
    assert leakage_summary["Share"].sum() == pytest.approx(1)
    atlas_leakage = leakage_summary.set_index("Leakage category")["Amount"]
    assert atlas_leakage["Base rate inflation"] == pytest.approx(60 + 10 * (10 / 15))
    assert atlas_leakage["Accessorial creep"] == pytest.approx(5 * (10 / 15))


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

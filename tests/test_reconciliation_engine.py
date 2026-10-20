import pandas as pd
import pytest

from src.reconciliation_engine import (
    DISCREPANCY_FLAGGED,
    FUEL_INDEX_MAP,
    REVIEW_INCOMPLETE,
    REVIEW_REQUIRED,
    TOUCHLESS_APPROVED,
    UNAUTHORIZED_FUEL_CREEP,
    calculate_authorized_fuel,
    reconcile,
    summarize_carrier_billing,
    summarize,
    write_discrepancy_queue,
)


def sources(receipt_weight=1000, billed_weight=1000, receipt=True):
    po = pd.DataFrame([{
        "po_number": "PO-1", "vendor_id": "V-1", "carrier_name": "Carrier",
        "origin": "A", "destination": "B", "contracted_base_freight": 1000,
        "agreed_fuel_surcharge": 45, "max_accessorial_allowance": 50,
    }])
    invoice = pd.DataFrame([{
        "invoice_number": "INV-1", "po_number": "PO-1", "carrier_name": "Carrier",
        "billed_base_freight": 1000, "billed_fuel_surcharge": 45,
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


def test_base_underbill_is_favorable_and_not_queued(tmp_path):
    po, invoice, receipts = sources()
    po.loc[0, "contracted_base_freight"] = 100
    invoice.loc[0, "billed_base_freight"] = 95
    invoice.loc[0, "billed_fuel_surcharge"] = 0
    invoice.loc[0, "billed_accessorial_fee"] = 0

    result = reconcile(po, invoice, receipts)

    assert result.loc[0, "base_variance"] == -5
    assert result.loc[0, "total_overbill"] == 0
    assert result.loc[0, "disputed_amount"] == 0
    assert result.loc[0, "total_invoiced"] == 95
    assert result.loc[0, "approved_remittance"] == 95
    assert result.loc[0, "status"] == TOUCHLESS_APPROVED
    assert write_discrepancy_queue(result, tmp_path / "queue.json") == []


def test_underbill_does_not_offset_a_different_line_item_overcharge():
    po, invoice, receipts = sources()
    po.loc[0, "contracted_base_freight"] = 1000
    po["approved_accessorial_items"] = [[
        {"code": "LFT", "description": "Liftgate", "amount": 100}
    ]]
    invoice.loc[0, "billed_base_freight"] = 1000
    invoice.loc[0, "billed_fuel_surcharge"] = 50
    invoice.loc[0, "billed_accessorial_fee"] = 95
    invoice["accessorial_items"] = [[
        {"code": "LFT", "description": "Liftgate", "amount": 95}
    ]]

    result = reconcile(po, invoice, receipts)

    assert result.loc[0, "base_variance"] == 0
    assert result.loc[0, "fuel_variance"] == 5
    assert result.loc[0, "accessorial_variance"] == -5
    assert result.loc[0, "disputed_variance"] == 5
    assert result.loc[0, "disputed_amount"] == 5
    assert result.loc[0, "total_invoiced"] == 1145
    assert result.loc[0, "approved_remittance"] == 1140
    assert result.loc[0, "unauthorized_accessorial_items"] == []
    assert result.loc[0, "status"] == DISCREPANCY_FLAGGED
    assert result.loc[0, "overcharge_line_items"] == [
        {
            "description": "Fuel surcharge",
            "amount": 5.0,
            "is_overcharge": True,
        }
    ]


def test_aip_fuel_calculation_uses_weekly_index_and_decimal_steps():
    authorization = calculate_authorized_fuel(2750, "2026-10-15")

    assert authorization == {
        "index_price": FUEL_INDEX_MAP["2026-10-12"],
        "base_peg": 1.5,
        "steps": 9,
        "allowed_pct": 0.045,
        "authorized_amount": 123.75,
    }

    fallback = calculate_authorized_fuel(2750, "2026-11-02")
    assert fallback["index_price"] == 1.95
    assert fallback["steps"] == 9


@pytest.mark.parametrize(
    ("week", "index_price", "steps", "allowed_pct", "authorized_amount"),
    [
        ("2026-10-05", 1.88, 7, 0.035, 35.0),
        ("2026-10-19", 1.92, 8, 0.04, 40.0),
    ],
)
def test_aip_calculation_uses_each_published_benchmark(
    week, index_price, steps, allowed_pct, authorized_amount
):
    authorization = calculate_authorized_fuel(1000, week)

    assert authorization["index_price"] == index_price
    assert authorization["steps"] == steps
    assert authorization["allowed_pct"] == allowed_pct
    assert authorization["authorized_amount"] == authorized_amount


def test_aip_fuel_overcharge_drives_signed_variance_remittance_and_queue(tmp_path):
    po, invoice, receipts = sources()
    po.loc[0, "contracted_base_freight"] = 2750
    po["agreed_fuel_surcharge"] = [247.5]
    po.loc[0, "max_accessorial_allowance"] = 90
    po.loc[0, "contracted_expected_weight"] = 14000
    po["approved_accessorial_items"] = [[{"code": "LFT", "amount": 90}]]
    invoice.loc[0, "billed_base_freight"] = 3180
    invoice.loc[0, "billed_fuel_surcharge"] = 285
    invoice.loc[0, "invoice_total_due"] = 3645
    invoice.loc[0, "billed_accessorial_fee"] = 180
    invoice.loc[0, "billed_weight_lbs"] = 15000
    invoice["accessorial_items"] = [[
        {"code": "LFT", "amount": 90},
        {"code": "DET", "amount": 90},
    ]]
    receipts.loc[0, "actual_scale_weight_lbs"] = 14000

    result = reconcile(po, invoice, receipts)
    queue = write_discrepancy_queue(result, tmp_path / "queue.json")

    assert result.loc[0, "authorized_fuel_surcharge"] == 123.75
    assert result.loc[0, "fuel_variance"] == pytest.approx(161.25)
    assert result.loc[0, "status"] == DISCREPANCY_FLAGGED
    assert result.loc[0, "root_cause"] == UNAUTHORIZED_FUEL_CREEP
    assert result.loc[0, "disputed_amount"] == pytest.approx(681.25)
    assert result.loc[0, "approved_remittance"] == pytest.approx(2963.75)
    assert queue[0]["root_cause"] == UNAUTHORIZED_FUEL_CREEP


def test_weight_tolerance_is_two_percent():
    within = reconcile(*sources(receipt_weight=1000, billed_weight=1020))
    over = reconcile(*sources(receipt_weight=1000, billed_weight=1021))
    assert within.loc[0, "status"] == TOUCHLESS_APPROVED
    assert over.loc[0, "status"] == REVIEW_REQUIRED
    assert over.loc[0, "weight_variance_lbs"] == pytest.approx(1)


def test_fuel_overcharge_within_tolerance_is_approved_without_withholding():
    po, invoice, receipts = sources()
    invoice["billed_fuel_surcharge"] = 45.5

    result = reconcile(po, invoice, receipts)

    assert result.loc[0, "fuel_variance"] == pytest.approx(0.5)
    assert result.loc[0, "fuel_overcharge"] == pytest.approx(0.5)
    assert result.loc[0, "fuel_disputed_overcharge"] == 0
    assert result.loc[0, "disputed_variance"] == 0
    assert result.loc[0, "disputed_amount"] == 0
    assert result.loc[0, "approved_remittance"] == result.loc[0, "total_invoiced"]
    assert result.loc[0, "status"] == TOUCHLESS_APPROVED


def test_fuel_tolerance_boundary_passes_and_above_boundary_flags():
    po, invoice, receipts = sources()
    invoice["billed_fuel_surcharge"] = 46

    within = reconcile(po, invoice, receipts, fuel_tolerance=1.0)
    above = reconcile(po, invoice, receipts, fuel_tolerance=0.99)

    assert within.loc[0, "fuel_variance"] == pytest.approx(1.0)
    assert within.loc[0, "status"] == TOUCHLESS_APPROVED
    assert within.loc[0, "disputed_amount"] == 0
    assert within.loc[0, "fuel_disputed_overcharge"] == 0
    assert above.loc[0, "status"] == DISCREPANCY_FLAGGED
    assert above.loc[0, "disputed_amount"] == pytest.approx(1.0)
    assert above.loc[0, "fuel_disputed_overcharge"] == pytest.approx(1.0)


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
    assert result.loc[0, "fuel_variance"] == 135
    assert result.loc[0, "fuel_overcharge"] == 135
    assert result.loc[0, "accessorial_variance"] == 50
    assert result.loc[0, "total_overbill"] == 185
    assert result.loc[0, "status"] == DISCREPANCY_FLAGGED


def test_short_pay_amounts_use_invoice_total_and_disputed_overbill():
    po, invoice, receipts = sources()
    invoice["billed_base_freight"] = 1100
    invoice["invoice_total_due"] = 1260

    result = reconcile(po, invoice, receipts)

    assert result.loc[0, "total_invoiced"] == 1195
    assert result.loc[0, "disputed_amount"] == 100
    assert result.loc[0, "approved_remittance"] == 1095


def test_duplicate_invoice_number_blocks_later_invoice_and_identifies_original():
    po, invoices, receipts = sources()
    invoices["invoice_id"] = ["invoice-original"]
    duplicate = invoices.iloc[0].copy()
    duplicate["invoice_id"] = "invoice-duplicate"
    invoices = pd.concat([invoices, duplicate.to_frame().T], ignore_index=True)

    result = reconcile(po, invoices, receipts)

    assert result["status"].tolist() == [
        TOUCHLESS_APPROVED,
        "DUPLICATE_BILLING_BLOCKED",
    ]
    assert result.loc[1, "duplicate_of_invoice_id"] == "invoice-original"


def test_duplicate_triplet_blocks_altered_invoice_number_and_queue_has_no_approval(tmp_path):
    po, invoices, receipts = sources()
    invoices["invoice_id"] = ["invoice-original"]
    invoices["bol_number"] = ["BOL-1007"]
    duplicate = invoices.iloc[0].copy()
    duplicate["invoice_id"] = "invoice-duplicate"
    duplicate["invoice_number"] = "INV-1007-DUP"
    duplicate["carrier_name"] = " carrier "
    invoices = pd.concat([invoices, duplicate.to_frame().T], ignore_index=True)

    result = reconcile(po, invoices, receipts)
    queue = write_discrepancy_queue(result, tmp_path / "duplicate-queue.json")

    assert result.loc[1, "status"] == "DUPLICATE_BILLING_BLOCKED"
    assert result.loc[1, "duplicate_of_invoice_id"] == "invoice-original"
    blocked_record = next(row for row in queue if row["invoice_id"] == "invoice-duplicate")
    assert blocked_record["duplicate_of_invoice_id"] == "invoice-original"
    assert blocked_record["actions"] == ["review_duplicate"]


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
    assert result.loc[0, "accessorial_overcharge"] == 90
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
                "total_overbill": 15,
            },
        ]
    )

    carrier_summary, leakage_summary = summarize_carrier_billing(audited)

    northstar = carrier_summary.set_index("Carrier").loc["Northstar"]
    assert northstar["Invoices"] == 2
    assert northstar["Clean invoices"] == 1
    assert northstar["Accuracy index"] == pytest.approx(0.5)
    assert northstar["Primary leakage driver"] == "Base rate inflation"
    assert leakage_summary["Amount"].sum() == pytest.approx(85)
    assert leakage_summary["Share"].sum() == pytest.approx(1)
    atlas_leakage = leakage_summary.set_index("Leakage category")["Amount"]
    assert atlas_leakage["Base rate inflation"] == pytest.approx(60 + 10)
    assert atlas_leakage["Accessorial creep"] == pytest.approx(5)


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

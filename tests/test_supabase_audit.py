import pandas as pd

from src.supabase_audit import AUTO_APPROVED, DISPUTED, REQUIRES_REVIEW, reconcile_supabase_records


def invoice(invoice_id="INV-1", shipment_id="LOAD-1", accessorial_fees=20, billed_weight=1000):
    return {
        "invoice_id": invoice_id,
        "carrier_name": "Northstar Freight",
        "shipment_id": shipment_id,
        "billed_weight": billed_weight,
        "base_rate": 100,
        "fuel_surcharge": 10,
        "accessorial_fees": accessorial_fees,
    }


def dispatch(shipment_id="LOAD-1", approved_accessorials=20, expected_weight=1000):
    return {
        "shipment_id": shipment_id,
        "carrier_name": "Northstar Freight",
        "expected_weight": expected_weight,
        "contracted_base_rate": 100,
        "contracted_fuel_surcharge": 10,
        "approved_accessorials": approved_accessorials,
        "expected_total": 100 + 10 + approved_accessorials,
    }


def test_exact_total_is_auto_approved():
    result = reconcile_supabase_records(pd.DataFrame([invoice()]), pd.DataFrame([dispatch()]))
    assert result.loc[0, "status"] == AUTO_APPROVED
    assert result.loc[0, "total_variance"] == 0


def test_unapproved_accessorial_is_disputed():
    result = reconcile_supabase_records(
        pd.DataFrame([invoice("INV-1004", accessorial_fees=150)]),
        pd.DataFrame([dispatch(approved_accessorials=50)]),
    )
    assert result.loc[0, "status"] == DISPUTED
    assert result.loc[0, "accessorial_overage"] == 100


def test_small_total_and_weight_variances_require_review():
    result = reconcile_supabase_records(
        pd.DataFrame([invoice(accessorial_fees=20.5, billed_weight=1020)]),
        pd.DataFrame([dispatch(expected_weight=1000)]),
    )
    assert result.loc[0, "status"] == REQUIRES_REVIEW


def test_offsetting_component_variances_do_not_auto_approve():
    bill = invoice()
    bill["base_rate"] = 100.5
    bill["fuel_surcharge"] = 9.5
    result = reconcile_supabase_records(pd.DataFrame([bill]), pd.DataFrame([dispatch()]))
    assert result.loc[0, "total_variance"] == 0
    assert result.loc[0, "status"] == REQUIRES_REVIEW


def test_missing_shipment_id_uses_carrier_and_rate_fallback():
    result = reconcile_supabase_records(
        pd.DataFrame([invoice(shipment_id="UNKNOWN")]),
        pd.DataFrame([dispatch(shipment_id="LOAD-1")]),
    )
    assert result.loc[0, "dispatch_shipment_id"] == "LOAD-1"
    assert result.loc[0, "status"] == AUTO_APPROVED
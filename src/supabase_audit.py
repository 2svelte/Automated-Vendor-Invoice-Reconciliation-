"""Join Supabase invoice and dispatch rows and calculate audit outcomes."""

from __future__ import annotations

import pandas as pd

AUTO_APPROVED = "AUTO-APPROVED"
DISPUTED = "DISPUTED"
REQUIRES_REVIEW = "REQUIRES REVIEW"

INVOICE_COLUMNS = {
    "invoice_id", "carrier_name", "shipment_id", "billed_weight", "base_rate",
    "fuel_surcharge", "accessorial_fees",
}
DISPATCH_COLUMNS = {
    "shipment_id", "carrier_name", "expected_weight", "contracted_base_rate",
    "contracted_fuel_surcharge", "approved_accessorials", "expected_total",
}


def reconcile_supabase_records(
    invoices: pd.DataFrame,
    dispatches: pd.DataFrame,
    *,
    amount_tolerance: float = 1.0,
    weight_tolerance: float = 50.0,
    rate_match_tolerance: float = 1.0,
) -> pd.DataFrame:
    """Join bills to dispatch baselines and assign deterministic audit statuses."""
    missing_invoice_columns = INVOICE_COLUMNS - set(invoices.columns)
    missing_dispatch_columns = DISPATCH_COLUMNS - set(dispatches.columns)
    if missing_invoice_columns or missing_dispatch_columns:
        raise ValueError(
            f"Missing invoice columns: {sorted(missing_invoice_columns)}; "
            f"missing dispatch columns: {sorted(missing_dispatch_columns)}"
        )

    invoice_rows = invoices.copy()
    dispatch_rows = dispatches.copy().rename(
        columns={"shipment_id": "dispatch_shipment_id", "carrier_name": "dispatch_carrier_name"}
    )
    invoice_rows["_carrier_key"] = invoice_rows["carrier_name"].astype("string").str.strip().str.casefold()
    dispatch_rows["_carrier_key"] = dispatch_rows["dispatch_carrier_name"].astype("string").str.strip().str.casefold()

    invoice_numeric = ["billed_weight", "base_rate", "fuel_surcharge", "accessorial_fees", "total_amount"]
    dispatch_numeric = [
        "expected_weight", "contracted_base_rate", "contracted_fuel_surcharge",
        "approved_accessorials", "expected_total",
    ]
    for column in invoice_numeric:
        if column in invoice_rows:
            invoice_rows[column] = pd.to_numeric(invoice_rows[column], errors="coerce")
    for column in dispatch_numeric:
        dispatch_rows[column] = pd.to_numeric(dispatch_rows[column], errors="coerce")

    if "total_amount" not in invoice_rows:
        invoice_rows["total_amount"] = (
            invoice_rows["base_rate"]
            + invoice_rows["fuel_surcharge"]
            + invoice_rows["accessorial_fees"].fillna(0)
        )
    else:
        calculated_total = (
            invoice_rows["base_rate"]
            + invoice_rows["fuel_surcharge"]
            + invoice_rows["accessorial_fees"].fillna(0)
        )
        invoice_rows["total_amount"] = invoice_rows["total_amount"].fillna(calculated_total)

    direct = invoice_rows.merge(
        dispatch_rows.drop(columns="_carrier_key"),
        how="left",
        left_on="shipment_id",
        right_on="dispatch_shipment_id",
        indicator="_shipment_match",
    )
    direct_matches = direct[direct["_shipment_match"].eq("both")].drop(columns="_shipment_match")
    unmatched_invoices = direct.loc[
        direct["_shipment_match"].eq("left_only"), invoice_rows.columns
    ].copy()

    fallback_candidates = unmatched_invoices.merge(
        dispatch_rows, how="inner", on="_carrier_key", suffixes=("", "_dispatch")
    )
    if not fallback_candidates.empty:
        fallback_candidates["_rate_distance"] = (
            fallback_candidates["base_rate"] - fallback_candidates["contracted_base_rate"]
        ).abs()
        fallback_matches = fallback_candidates.loc[
            fallback_candidates["_rate_distance"].le(rate_match_tolerance)
        ].sort_values(["_rate_distance", "invoice_id", "dispatch_shipment_id"])
        fallback_matches = fallback_matches.drop_duplicates("invoice_id").drop_duplicates("dispatch_shipment_id")
        fallback_matches = fallback_matches.drop(columns="_rate_distance")
    else:
        fallback_matches = pd.DataFrame()

    matched_invoice_ids = set(direct_matches["invoice_id"])
    if not fallback_matches.empty:
        matched_invoice_ids.update(fallback_matches["invoice_id"])
    unmatched_rows = invoice_rows.loc[~invoice_rows["invoice_id"].isin(matched_invoice_ids)].copy()
    for column in dispatch_rows.columns:
        if column not in unmatched_rows:
            unmatched_rows[column] = pd.NA

    audited = pd.concat([direct_matches, fallback_matches, unmatched_rows], ignore_index=True, sort=False)
    audited["base_rate_variance"] = audited["base_rate"] - audited["contracted_base_rate"]
    audited["weight_variance"] = audited["billed_weight"] - audited["expected_weight"]
    audited["accessorial_variance"] = audited["accessorial_fees"] - audited["approved_accessorials"]
    audited["accessorial_overage"] = audited["accessorial_variance"].clip(lower=0)
    audited["total_variance"] = audited["total_amount"] - audited["expected_total"]

    def status_for(row: pd.Series) -> str:
        variances = row[["base_rate_variance", "weight_variance", "accessorial_variance", "total_variance"]]
        if variances.isna().any():
            return REQUIRES_REVIEW
        if (
            row["base_rate_variance"] > amount_tolerance
            or row["accessorial_overage"] > amount_tolerance
            or row["weight_variance"] > weight_tolerance
            or row["total_variance"] > amount_tolerance
        ):
            return DISPUTED
        if (
            round(float(row["total_variance"]), 2) == 0
            and row["base_rate_variance"] == 0
            and row["weight_variance"] == 0
            and row["accessorial_variance"] == 0
        ):
            return AUTO_APPROVED
        return REQUIRES_REVIEW

    audited["status"] = audited.apply(status_for, axis=1)
    return audited.drop(columns=["_carrier_key", "dispatch_carrier_key"], errors="ignore").sort_values(
        "invoice_id", kind="stable"
    ).reset_index(drop=True)
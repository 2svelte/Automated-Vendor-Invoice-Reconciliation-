"""Deterministic three-way freight invoice reconciliation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

import pandas as pd

TOUCHLESS_APPROVED = "TOUCHLESS_APPROVED"
REVIEW_REQUIRED = "REVIEW_REQUIRED"
DISCREPANCY_FLAGGED = "DISCREPANCY_FLAGGED"
REVIEW_INCOMPLETE = "REVIEW_INCOMPLETE"

REQUIRED_COLUMNS = {
    "purchase_orders": {
        "po_number", "vendor_id", "carrier_name", "origin", "destination",
        "contracted_base_freight", "agreed_fuel_surcharge", "max_accessorial_allowance",
    },
    "invoices": {
        "invoice_number", "po_number", "carrier_name", "billed_base_freight",
        "billed_fuel_surcharge", "billed_accessorial_fee", "billed_weight_lbs", "invoice_date",
    },
    "receipts": {"po_number", "delivery_timestamp", "dock_signature", "actual_scale_weight_lbs"},
}

NUMERIC_COLUMNS = {
    "contracted_base_freight", "agreed_fuel_surcharge", "max_accessorial_allowance",
    "billed_base_freight", "billed_fuel_surcharge", "billed_accessorial_fee",
    "billed_weight_lbs", "actual_scale_weight_lbs",
}


def _clean_columns(frame: pd.DataFrame) -> pd.DataFrame:
    cleaned = frame.copy()
    cleaned.columns = [str(column).strip().lower().replace(" ", "_") for column in cleaned.columns]
    for column in NUMERIC_COLUMNS.intersection(cleaned.columns):
        cleaned[column] = pd.to_numeric(
            cleaned[column].astype(str).str.replace(r"[$,]", "", regex=True), errors="coerce"
        )
    for column in {"po_number", "invoice_number", "carrier_name", "vendor_id", "dock_signature"}.intersection(cleaned.columns):
        cleaned[column] = cleaned[column].astype("string").str.strip()
    return cleaned


def _validate_inputs(frames: dict[str, pd.DataFrame]) -> None:
    missing_sources = set(REQUIRED_COLUMNS) - set(frames)
    if missing_sources:
        raise ValueError(f"Missing required sources: {', '.join(sorted(missing_sources))}")
    for source_name, required in REQUIRED_COLUMNS.items():
        missing_columns = required - set(frames[source_name].columns.str.lower())
        if missing_columns:
            raise ValueError(f"{source_name} is missing columns: {', '.join(sorted(missing_columns))}")


def reconcile(
    purchase_orders: pd.DataFrame,
    invoices: pd.DataFrame,
    receipts: pd.DataFrame,
) -> pd.DataFrame:
    """Merge sources and calculate deterministic variances for each invoice."""
    raw_frames = {"purchase_orders": purchase_orders, "invoices": invoices, "receipts": receipts}
    _validate_inputs(raw_frames)
    frames = {name: _clean_columns(frame) for name, frame in raw_frames.items()}

    merged = frames["invoices"].merge(frames["purchase_orders"], on="po_number", how="left", suffixes=("", "_po"))
    merged = merged.merge(frames["receipts"], on="po_number", how="left", indicator="receipt_match")
    merged["receipt_found"] = merged["receipt_match"].eq("both")

    merged["base_variance"] = merged["billed_base_freight"] - merged["contracted_base_freight"]
    merged["fuel_variance"] = merged["billed_fuel_surcharge"] - merged["agreed_fuel_surcharge"]
    merged["accessorial_variance"] = (
        merged["billed_accessorial_fee"] - merged["max_accessorial_allowance"]
    ).clip(lower=0)
    merged["total_overbill"] = (
        merged["base_variance"] + merged["fuel_variance"] + merged["accessorial_variance"]
    ).clip(lower=0)
    merged["total_contracted_value"] = (
        merged["contracted_base_freight"]
        + merged["agreed_fuel_surcharge"]
        + merged["max_accessorial_allowance"]
    )
    merged["variance_pct"] = merged["total_overbill"].div(merged["total_contracted_value"]).fillna(0)

    merged["weight_variance_lbs"] = (
        merged["billed_weight_lbs"] - (merged["actual_scale_weight_lbs"] * 1.02)
    ).clip(lower=0)
    merged["weight_variance_pct"] = (
        merged["billed_weight_lbs"].div(merged["actual_scale_weight_lbs"]).sub(1).fillna(0).clip(lower=0)
    )
    merged["weight_check_passed"] = merged["receipt_found"] & merged["weight_variance_lbs"].le(0)

    merged["status"] = REVIEW_REQUIRED
    merged.loc[merged["variance_pct"].le(0.01) & merged["receipt_found"] & merged["weight_check_passed"], "status"] = TOUCHLESS_APPROVED
    merged.loc[merged["variance_pct"].gt(0.05) | merged["weight_variance_lbs"].gt(0), "status"] = DISCREPANCY_FLAGGED
    merged.loc[~merged["receipt_found"], "status"] = REVIEW_INCOMPLETE
    return merged.drop(columns=["receipt_match"])


def reconcile_csvs(
    purchase_orders_path: str | Path,
    invoices_path: str | Path,
    receipts_path: str | Path,
) -> pd.DataFrame:
    """Load CSV inputs and reconcile them."""
    return reconcile(
        pd.read_csv(purchase_orders_path),
        pd.read_csv(invoices_path),
        pd.read_csv(receipts_path),
    )


def summarize(reconciled: pd.DataFrame) -> dict[str, float | int]:
    """Return dashboard KPIs from a reconciled frame."""
    audited_spend = reconciled["billed_base_freight"].fillna(0) + reconciled["billed_fuel_surcharge"].fillna(0)
    return {
        "invoice_count": int(len(reconciled)),
        "audited_spend": float(audited_spend.sum()),
        "touchless_count": int(reconciled["status"].eq(TOUCHLESS_APPROVED).sum()),
        "stp_rate": float(reconciled["status"].eq(TOUCHLESS_APPROVED).mean()) if len(reconciled) else 0.0,
        "prevented_overbill": float(reconciled["total_overbill"].fillna(0).sum()),
        "flagged_count": int(reconciled["status"].eq(DISCREPANCY_FLAGGED).sum()),
        "incomplete_count": int(reconciled["status"].eq(REVIEW_INCOMPLETE).sum()),
    }


def discrepancy_records(reconciled: pd.DataFrame) -> Iterable[dict]:
    """Yield JSON-friendly records for the dispute agent or workflow adapters."""
    flagged = reconciled[reconciled["status"].isin([DISCREPANCY_FLAGGED, REVIEW_INCOMPLETE])]
    return flagged.where(pd.notna(flagged), None).to_dict(orient="records")


def write_discrepancy_queue(reconciled: pd.DataFrame, output_path: str | Path) -> list[dict]:
    """Write flagged exceptions in the contract consumed by Power Automate."""
    def numeric(value: object) -> float:
        return 0.0 if value is None or pd.isna(value) else float(value)

    queue = []
    for record in discrepancy_records(reconciled):
        queue.append(
            {
                "invoice_number": record.get("invoice_number"),
                "po_number": record.get("po_number"),
                "carrier_name": record.get("carrier_name"),
                "status": record.get("status"),
                "total_overbill": round(numeric(record.get("total_overbill")), 2),
                "variance_pct": round(numeric(record.get("variance_pct")), 6),
                "base_variance": round(numeric(record.get("base_variance")), 2),
                "fuel_variance": round(numeric(record.get("fuel_variance")), 2),
                "accessorial_variance": round(numeric(record.get("accessorial_variance")), 2),
                "weight_variance_lbs": round(numeric(record.get("weight_variance_lbs")), 2),
                "receipt_found": bool(record.get("receipt_found")) if pd.notna(record.get("receipt_found")) else False,
                "actions": ["approve_overcharge", "send_ai_dispute"],
            }
        )
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(queue, indent=2), encoding="utf-8")
    return queue

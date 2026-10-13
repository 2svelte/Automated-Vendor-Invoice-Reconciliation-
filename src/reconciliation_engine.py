"""Deterministic three-way freight invoice reconciliation."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from pathlib import Path
from numbers import Real
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
    "contracted_base_rate", "contracted_fuel_surcharge", "contracted_expected_weight",
    "approved_accessorial_allowance", "billed_chargeable_weight", "certified_net_scale_weight",
}


def _clean_columns(frame: pd.DataFrame) -> pd.DataFrame:
    cleaned = frame.copy()
    cleaned.columns = [str(column).strip().lower().replace(" ", "_") for column in cleaned.columns]
    for column in NUMERIC_COLUMNS.intersection(cleaned.columns):
        cleaned[column] = pd.to_numeric(
            cleaned[column].astype(str).str.replace(r"[$,]", "", regex=True), errors="coerce"
        )
    for column in {
        "po_number",
        "invoice_number",
        "carrier_name",
        "authorized_carrier",
        "vendor_id",
        "dock_signature",
    }.intersection(cleaned.columns):
        cleaned[column] = cleaned[column].astype("string").str.strip()
    return cleaned


def _normalize_source_columns(name: str, frame: pd.DataFrame) -> pd.DataFrame:
    aliases = {
        "purchase_orders": {
            "carrier_name": ("authorized_carrier",),
            "contracted_base_freight": ("contracted_base_rate",),
            "agreed_fuel_surcharge": ("contracted_fuel_surcharge",),
            "max_accessorial_allowance": ("approved_accessorial_allowance",),
        },
        "invoices": {
            "billed_base_freight": ("base_rate",),
            "billed_fuel_surcharge": ("fuel_surcharge",),
            "billed_weight_lbs": ("billed_chargeable_weight",),
            "invoice_date": ("issue_date",),
        },
        "receipts": {
            "delivery_timestamp": ("received_timestamp",),
            "actual_scale_weight_lbs": ("certified_net_scale_weight",),
        },
    }
    normalized = frame.copy()
    for canonical, candidates in aliases.get(name, {}).items():
        if canonical not in normalized:
            for candidate in candidates:
                if candidate in normalized:
                    normalized[canonical] = normalized[candidate]
                    break
    return normalized


def _accessorial_items(value: object) -> list[Mapping[str, object]]:
    if value is None or value is pd.NA:
        return []
    if isinstance(value, float) and math.isnan(value):
        return []
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, list):
        raise ValueError("Accessorial items must be provided as a JSON array.")
    if any(not isinstance(item, Mapping) for item in value):
        raise ValueError("Each accessorial item must be a JSON object.")
    return value


def _itemized_accessorial_variance(
    billed_value: object,
    approved_value: object,
    billed_total: object,
    approved_total: object,
) -> tuple[float, list[dict[str, object]]]:
    billed_items = _accessorial_items(billed_value)
    approved_items = _accessorial_items(approved_value)
    legacy_allowance = pd.to_numeric(approved_total, errors="coerce")
    use_legacy_allowance = (
        not approved_items
        and pd.notna(legacy_allowance)
        and float(legacy_allowance) > 0
    )
    if not billed_items or use_legacy_allowance:
        billed = pd.to_numeric(billed_total, errors="coerce")
        allowance = legacy_allowance if pd.notna(legacy_allowance) else 0.0
        if pd.isna(billed):
            return float("nan"), []
        return max(0.0, float(billed) - float(allowance)), []

    approved_by_code: dict[str, float] = {}
    for item in approved_items:
        code = str(item.get("code", "")).strip().upper()
        amount = pd.to_numeric(item.get("amount"), errors="coerce")
        if pd.isna(amount):
            raise ValueError(f"Approved accessorial {code or '<unknown>'} has no valid amount.")
        approved_by_code[code] = approved_by_code.get(code, 0.0) + float(amount)

    billed_by_code: dict[str, dict[str, object]] = {}
    for item in billed_items:
        code = str(item.get("code", "")).strip().upper()
        amount = pd.to_numeric(item.get("amount"), errors="coerce")
        if pd.isna(amount):
            raise ValueError(f"Billed accessorial {code or '<unknown>'} has no valid amount.")
        line = billed_by_code.setdefault(
            code,
            {
                "code": code,
                "description": str(item.get("description", "")).strip(),
                "billed_amount": 0.0,
            },
        )
        line["billed_amount"] = float(line["billed_amount"]) + float(amount)

    unauthorized: list[dict[str, object]] = []
    for code, line in billed_by_code.items():
        authorized_amount = approved_by_code.get(code, 0.0)
        billed_amount = float(line["billed_amount"])
        overage = max(0.0, billed_amount - authorized_amount)
        if overage > 0:
            unauthorized.append(
                {
                    **line,
                    "authorized_amount": authorized_amount,
                    "overage": overage,
                }
            )
    return sum(float(item["overage"]) for item in unauthorized), unauthorized


def _validate_inputs(frames: dict[str, pd.DataFrame]) -> None:
    missing_sources = set(REQUIRED_COLUMNS) - set(frames)
    if missing_sources:
        raise ValueError(f"Missing required sources: {', '.join(sorted(missing_sources))}")
    alternatives = {
        "purchase_orders": {
            "carrier_name": {"carrier_name", "authorized_carrier"},
            "contracted_base_freight": {"contracted_base_freight", "contracted_base_rate"},
            "agreed_fuel_surcharge": {"agreed_fuel_surcharge", "contracted_fuel_surcharge"},
            "max_accessorial_allowance": {
                "max_accessorial_allowance",
                "approved_accessorial_allowance",
            },
        },
        "invoices": {
            "billed_base_freight": {"billed_base_freight", "base_rate"},
            "billed_fuel_surcharge": {"billed_fuel_surcharge", "fuel_surcharge"},
            "billed_weight_lbs": {"billed_weight_lbs", "billed_chargeable_weight"},
            "invoice_date": {"invoice_date", "issue_date"},
        },
        "receipts": {
            "delivery_timestamp": {"delivery_timestamp", "received_timestamp"},
            "actual_scale_weight_lbs": {"actual_scale_weight_lbs", "certified_net_scale_weight"},
        },
    }
    for source_name, required in REQUIRED_COLUMNS.items():
        columns = set(frames[source_name].columns.str.lower())
        missing_columns = {
            column
            for column in required
            if not columns.intersection(alternatives.get(source_name, {}).get(column, {column}))
        }
        if missing_columns:
            raise ValueError(f"{source_name} is missing columns: {', '.join(sorted(missing_columns))}")


def reconcile(
    purchase_orders: pd.DataFrame,
    invoices: pd.DataFrame,
    receipts: pd.DataFrame,
    *,
    fuel_tolerance: float = 1.0,
    weight_tolerance_pct: float = 2.0,
) -> pd.DataFrame:
    """Merge sources and calculate deterministic variances for each invoice."""
    if fuel_tolerance < 0:
        raise ValueError("fuel_tolerance must be zero or greater")
    if weight_tolerance_pct < 0:
        raise ValueError("weight_tolerance_pct must be zero or greater")

    raw_frames = {"purchase_orders": purchase_orders, "invoices": invoices, "receipts": receipts}
    frames = {
        name: _normalize_source_columns(name, _clean_columns(frame))
        for name, frame in raw_frames.items()
    }
    _validate_inputs(frames)

    merged = frames["invoices"].merge(frames["purchase_orders"], on="po_number", how="left", suffixes=("", "_po"))
    merged = merged.merge(
        frames["receipts"],
        on="po_number",
        how="left",
        indicator="receipt_match",
        suffixes=("", "_receipt"),
    )
    merged["receipt_found"] = merged["receipt_match"].eq("both")

    merged["base_variance"] = merged["billed_base_freight"] - merged["contracted_base_freight"]
    merged["fuel_variance"] = merged["billed_fuel_surcharge"] - merged["agreed_fuel_surcharge"]
    accessorial_results = merged.apply(
        lambda row: _itemized_accessorial_variance(
            row.get("accessorial_items"),
            row.get("approved_accessorial_items"),
            row.get("billed_accessorial_fee"),
            row.get("max_accessorial_allowance"),
        ),
        axis=1,
    )
    merged["accessorial_variance"] = accessorial_results.map(lambda result: result[0])
    merged["unauthorized_accessorial_items"] = accessorial_results.map(lambda result: result[1])
    merged["total_overbill"] = (
        merged["base_variance"] + merged["fuel_variance"] + merged["accessorial_variance"]
    ).clip(lower=0)
    merged["total_contracted_value"] = (
        merged["contracted_base_freight"]
        + merged["agreed_fuel_surcharge"]
        + merged["max_accessorial_allowance"]
    )
    merged["variance_pct"] = merged["total_overbill"].div(merged["total_contracted_value"]).fillna(0)

    fuel_over_tolerance = (merged["fuel_variance"] - fuel_tolerance).clip(lower=0)
    tolerance_adjusted_overbill = (
        merged["base_variance"] + fuel_over_tolerance + merged["accessorial_variance"]
    ).clip(lower=0)
    tolerance_adjusted_pct = (
        tolerance_adjusted_overbill.div(merged["total_contracted_value"]).fillna(0)
    )
    merged["weight_variance_lbs"] = (
        merged["billed_weight_lbs"]
        - (merged["actual_scale_weight_lbs"] * (1 + weight_tolerance_pct / 100))
    ).clip(lower=0)
    merged["weight_variance_pct"] = (
        merged["billed_weight_lbs"].div(merged["actual_scale_weight_lbs"]).sub(1).fillna(0).clip(lower=0)
    )
    merged["weight_check_passed"] = (
        merged["receipt_found"] & merged["weight_variance_lbs"].le(0)
    )

    merged["status"] = REVIEW_REQUIRED
    merged.loc[
        tolerance_adjusted_pct.le(0.01)
        & merged["receipt_found"]
        & merged["weight_check_passed"],
        "status",
    ] = TOUCHLESS_APPROVED
    merged.loc[
        tolerance_adjusted_pct.gt(0.05) | merged["weight_variance_lbs"].gt(0),
        "status",
    ] = DISCREPANCY_FLAGGED
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


def summarize_carrier_billing(
    reconciled: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Summarize exact billing accuracy by carrier and attribute net overbill."""
    variance_columns = {
        "Base rate inflation": "base_variance",
        "Fuel index slippage": "fuel_variance",
        "Accessorial creep": "accessorial_variance",
    }
    variances = reconciled[list(variance_columns.values())].apply(
        pd.to_numeric, errors="coerce"
    )
    clean = variances.eq(0).all(axis=1)
    positive_variances = variances.fillna(0).clip(lower=0)
    positive_total = positive_variances.sum(axis=1)
    net_overbill = (
        pd.to_numeric(reconciled["total_overbill"], errors="coerce")
        .fillna(0)
        .clip(lower=0)
    )
    allocation = net_overbill.div(positive_total.where(positive_total.gt(0), 1)).where(
        positive_total.gt(0), 0
    )
    attributed_variances = positive_variances.mul(allocation, axis=0)
    carrier_names = reconciled["carrier_name"].fillna("Unknown carrier").astype(str)

    carrier_rows = pd.DataFrame(
        {
            "Carrier": carrier_names,
            "Clean": clean,
            "Overbill exposure": net_overbill,
        }
    )
    carrier_summary = (
        carrier_rows.groupby("Carrier", as_index=False, sort=False)
        .agg(
            Invoices=("Clean", "size"),
            **{
                "Clean invoices": ("Clean", "sum"),
                "Overbill exposure": ("Overbill exposure", "sum"),
            },
        )
    )
    carrier_summary["Accuracy index"] = (
        carrier_summary["Clean invoices"] / carrier_summary["Invoices"]
    )

    carrier_variances = attributed_variances.copy()
    carrier_variances["Carrier"] = carrier_names
    carrier_variances = carrier_variances.groupby("Carrier", sort=False).sum()
    variance_labels = {
        variance_column: label for label, variance_column in variance_columns.items()
    }
    driver_by_carrier = {
        carrier: (
            variance_labels[values.idxmax()]
            if values.max() > 0
            else "No overbill detected"
        )
        for carrier, values in carrier_variances.iterrows()
    }
    carrier_summary["Primary leakage driver"] = carrier_summary["Carrier"].map(
        driver_by_carrier
    )
    carrier_summary = carrier_summary.sort_values(
        ["Overbill exposure", "Accuracy index", "Carrier"],
        ascending=[False, True, True],
        ignore_index=True,
    )

    category_amounts = (
        attributed_variances.sum()
        .rename(index=variance_labels)
        .rename("Amount")
        .rename_axis("Leakage category")
    )
    leakage_summary = category_amounts.reset_index()
    total_leakage = float(net_overbill.sum())
    leakage_summary["Share"] = (
        leakage_summary["Amount"] / total_leakage if total_leakage else 0.0
    )
    leakage_summary = leakage_summary.sort_values(
        "Amount", ascending=False, ignore_index=True
    )
    return carrier_summary, leakage_summary


def discrepancy_records(reconciled: pd.DataFrame) -> Iterable[dict]:
    """Yield JSON-friendly records for the dispute agent or workflow adapters."""
    flagged = reconciled[reconciled["status"].isin([DISCREPANCY_FLAGGED, REVIEW_INCOMPLETE])]
    return flagged.where(pd.notna(flagged), None).to_dict(orient="records")


def write_discrepancy_queue(reconciled: pd.DataFrame, output_path: str | Path) -> list[dict]:
    """Write flagged exceptions in the contract consumed by Power Automate."""
    def numeric(value: object) -> float:
        if not isinstance(value, Real):
            return 0.0
        number = float(value)
        return number if math.isfinite(number) else 0.0

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
                "unauthorized_accessorial_items": record.get(
                    "unauthorized_accessorial_items", []
                ),
                "weight_variance_lbs": round(numeric(record.get("weight_variance_lbs")), 2),
                "receipt_found": bool(record.get("receipt_found")) if pd.notna(record.get("receipt_found")) else False,
                "actions": ["approve_overcharge", "send_ai_dispute"],
            }
        )
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(queue, indent=2), encoding="utf-8")
    return queue

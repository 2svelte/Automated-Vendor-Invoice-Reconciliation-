"""Load and synchronize the three Supabase sources used by reconciliation."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src.reconciliation_engine import reconcile

AUDIT_STATUSES = {
    "UNDER_REVIEW",
    "OVERRIDE_APPROVED",
    "DISPUTED",
    "SCALE_TICKET_REQUESTED",
}

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_COLUMNS = {
    "purchase_orders": [
        "po_number", "vendor_id", "carrier_name", "origin", "destination",
        "contracted_base_freight", "agreed_fuel_surcharge", "max_accessorial_allowance",
    ],
    "carrier_invoices": [
        "invoice_number", "po_number", "carrier_name", "billed_base_freight",
        "billed_fuel_surcharge", "billed_accessorial_fee", "billed_weight_lbs", "invoice_date",
    ],
    "warehouse_goods_receipts": [
        "po_number", "delivery_timestamp", "dock_signature", "actual_scale_weight_lbs",
    ],
}


def create_supabase_client():
    """Create a Supabase client using the repo-root .env settings."""
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_KEY")
    if not supabase_url or not supabase_key or supabase_key.startswith("REPLACE_WITH_"):
        raise ValueError("Set a valid SUPABASE_URL and SUPABASE_KEY in the repo-root .env file.")

    from supabase import create_client

    return create_client(supabase_url, supabase_key)


def fetch_table(client, table_name: str, columns: list[str]) -> pd.DataFrame:
    """Read every row from a Supabase table, including paginated results."""
    rows: list[dict] = []
    offset = 0
    page_size = 1000
    while True:
        response = client.table(table_name).select("*").range(offset, offset + page_size - 1).execute()
        page = response.data or []
        rows.extend(page)
        if len(page) < page_size:
            break
        offset += page_size
    return pd.DataFrame(rows, columns=None if rows else columns)


def load_reconciliation_sources(client) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Fetch PO, invoice, and receipt rows from their canonical Supabase tables."""
    purchase_orders = fetch_table(client, "purchase_orders", SOURCE_COLUMNS["purchase_orders"])
    invoices = fetch_table(client, "carrier_invoices", SOURCE_COLUMNS["carrier_invoices"])
    receipts = fetch_table(
        client,
        "warehouse_goods_receipts",
        SOURCE_COLUMNS["warehouse_goods_receipts"],
    )
    return purchase_orders, invoices, receipts


def reconcile_supabase_data(
    client,
    *,
    fuel_tolerance: float = 1.0,
    weight_tolerance_pct: float = 2.0,
) -> pd.DataFrame:
    """Run the deterministic three-way engine against the current Supabase rows."""
    return reconcile(
        *load_reconciliation_sources(client),
        fuel_tolerance=fuel_tolerance,
        weight_tolerance_pct=weight_tolerance_pct,
    )


def update_invoice_audit(
    client,
    invoice_number: str,
    audit_status: str,
    auditor_name: str,
    audit_note: str,
) -> None:
    """Write a resolution to Supabase; a database trigger records the audit event."""
    if audit_status not in AUDIT_STATUSES:
        raise ValueError(f"Unsupported audit status: {audit_status}")
    if not invoice_number.strip():
        raise ValueError("invoice_number must not be empty")
    if not auditor_name.strip():
        raise ValueError("auditor_name must not be empty")
    if not audit_note.strip():
        raise ValueError("audit_note must not be empty")

    response = (
        client.table("carrier_invoices")
        .update(
            {
                "audit_status": audit_status,
                "auditor_name": auditor_name.strip(),
                "audit_note": audit_note.strip(),
                "audited_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        .eq("invoice_number", invoice_number)
        .select("invoice_number")
        .execute()
    )
    if not response.data:
        raise LookupError(f"Invoice {invoice_number} was not found in Supabase.")


def fetch_invoice_audit_events(client, invoice_number: str) -> list[dict[str, object]]:
    """Return newest-first, immutable audit events for one invoice."""
    response = (
        client.table("invoice_audit_events")
        .select("*")
        .eq("invoice_number", invoice_number)
        .order("created_at", desc=True)
        .execute()
    )
    return response.data or []


"""Load and synchronize the three Supabase sources used by reconciliation."""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

from src.reconciliation_engine import reconcile

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


def reconcile_supabase_data(client) -> pd.DataFrame:
    """Run the deterministic three-way engine against the current Supabase rows."""
    return reconcile(*load_reconciliation_sources(client))



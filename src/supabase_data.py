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
        "contracted_expected_weight", "contracted_base_rate", "contracted_fuel_surcharge",
        "authorized_carrier", "approved_accessorial_allowance",
        "approved_accessorial_items", "authorized_accessorial_codes",
        "incoterms", "freight_charge_terms", "payment_terms",
        "equipment_type_required", "temperature_controlled", "liftgate_required",
        "scheduled_pickup_window", "scheduled_delivery_window", "buyer_contact_email",
        "carrier_email",
    ],
    "carrier_invoices": [
        "invoice_number", "po_number", "carrier_name", "billed_base_freight",
        "billed_fuel_surcharge", "billed_accessorial_fee", "billed_weight_lbs", "invoice_date",
        "bol_number", "pro_number", "billed_chargeable_weight", "accessorial_items",
        "invoice_total_due", "bill_to_name", "bill_to_address", "remit_to_name",
        "remit_to_address", "payment_terms", "payment_due_date", "pickup_date",
        "delivery_date", "freight_class", "nmfc_code", "rate_tariff_reference",
        "rate_per_cwt", "handling_unit_count", "handling_unit_type",
        "carrier_email", "dispute_status", "dispute_from_email", "dispute_to_email",
        "dispute_cc_email", "dispute_subject",
        "dispute_memo_body", "dispute_queued_at", "email_dispatch_status", "dispatched_at",
    ],
    "warehouse_goods_receipts": [
        "po_number", "delivery_timestamp", "dock_signature", "actual_scale_weight_lbs",
        "receipt_id", "bol_number", "received_timestamp", "scale_ticket_id",
        "receiving_dock_door", "trailer_license_plate", "gross_weight_lbs", "tare_weight_lbs",
        "certified_net_scale_weight", "scale_certification_seal", "osd_notes",
        "driver_name", "driver_cdl_number", "carrier_scac", "tractor_unit_number",
        "security_seal_number", "security_seal_status", "scale_operator_id",
        "scale_calibration_date", "yard_check_in_timestamp",
        "dock_unloaded_timestamp", "yard_departure_timestamp",
        "received_pallet_count", "packaging_condition", "carrier_email",
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
    audit_note: str,
) -> None:
    """Write a resolution to Supabase; a database trigger records the audit event."""
    if audit_status not in AUDIT_STATUSES:
        raise ValueError(f"Unsupported audit status: {audit_status}")
    if not invoice_number.strip():
        raise ValueError("invoice_number must not be empty")

    response = (
        client.table("carrier_invoices")
        .update(
            {
                "audit_status": audit_status,
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


def _validate_email_recipients(value: str, field_name: str, *, required: bool) -> str:
    recipients = [address.strip() for address in value.split(",") if address.strip()]
    if required and not recipients:
        raise ValueError(f"{field_name} must include at least one email address.")
    for address in recipients:
        if (
            address.count("@") != 1
            or any(character.isspace() for character in address)
            or "." not in address.rsplit("@", maxsplit=1)[1]
        ):
            raise ValueError(f"{field_name} contains an invalid email address: {address}")
    return ", ".join(recipients)


def queue_dispute_email(
    client,
    invoice_number: str,
    *,
    from_email: str,
    to_email: str,
    cc_email: str,
    subject: str,
    body: str,
) -> None:
    """Queue a reviewed dispute email for Power Automate and record the dispute."""
    if not invoice_number.strip():
        raise ValueError("invoice_number must not be empty")
    from_email = _validate_email_recipients(from_email, "From", required=True)
    to_email = _validate_email_recipients(to_email, "To", required=True)
    cc_email = _validate_email_recipients(cc_email, "CC", required=False)
    if not subject.strip():
        raise ValueError("Subject must not be empty.")
    if not body.strip():
        raise ValueError("Message body must not be empty.")

    now = datetime.now(timezone.utc).isoformat()
    response = (
        client.table("carrier_invoices")
        .update(
            {
                "audit_status": "DISPUTED",
                "audit_note": "Dispute email queued for Outlook dispatch.",
                "audited_at": now,
                "dispute_status": "PENDING_DISPATCH",
                "dispute_from_email": from_email,
                "dispute_to_email": to_email,
                "dispute_cc_email": cc_email,
                "dispute_subject": subject.strip(),
                "dispute_memo_body": body,
                "dispute_queued_at": now,
                "email_dispatch_status": "PENDING",
            }
        )
        .eq("invoice_number", invoice_number)
        .in_("email_dispatch_status", ["NOT_QUEUED", "FAILED"])
        .select("invoice_number")
        .execute()
    )
    if not response.data:
        raise LookupError(
            f"Invoice {invoice_number} was not found, or its email is already pending/sent."
        )


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

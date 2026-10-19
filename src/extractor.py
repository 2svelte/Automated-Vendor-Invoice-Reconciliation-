from __future__ import annotations

import hashlib
import math
import os
import re
from datetime import date, timedelta
from pathlib import Path
from typing import TypeAlias

import pandas as pd
import pdfplumber

JSONValue: TypeAlias = (
    str | int | float | bool | None | list["JSONValue"] | dict[str, "JSONValue"]
)

EXPECTED_COLUMNS = [
    "invoice_number",
    "po_number",
    "bol_number",
    "pro_number",
    "carrier_name",
    "billed_chargeable_weight",
    "billed_base_freight",
    "billed_fuel_surcharge",
    "accessorial_items",
    "invoice_total_due",
    "billed_accessorial_fee",
    "billed_weight_lbs",
    "invoice_date",
    "bill_to_name",
    "bill_to_address",
    "remit_to_name",
    "remit_to_address",
    "payment_terms",
    "payment_due_date",
    "pickup_date",
    "delivery_date",
    "freight_class",
    "nmfc_code",
    "rate_tariff_reference",
    "rate_per_cwt",
    "handling_unit_count",
    "handling_unit_type",
]


def _clean_number(value: str | float | int) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = str(value).replace(",", "").replace("$", "").strip()
    if not cleaned:
        return 0.0
    return float(cleaned)


def _find_pattern(text: str, pattern: str, default: str = "") -> str:
    match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
    if not match:
        return default
    return match.group(1).strip()


def _extract_money(text: str, pattern: str, default: float = 0.0) -> float:
    match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
    if not match:
        return float(default)
    return _clean_number(match.group(1))


def _extract_weight(text: str, pattern: str, default: float = 0.0) -> float:
    match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
    if not match:
        return float(default)
    weight = _clean_number(match.group(1))
    unit = (match.group(2) or "lbs").lower()
    return round(weight / 0.45359237, 2) if unit == "kg" else weight


def parse_carrier_pdf(pdf_path: str | os.PathLike[str]) -> dict[str, object]:
    """Extract canonical invoice fields and compatibility aliases from a carrier PDF."""
    pdf_file = Path(pdf_path)
    with pdfplumber.open(str(pdf_file)) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)

    invoice_number = _find_pattern(text, r"Invoice\s*Number:\s*([A-Za-z0-9-]+)", default="UNKNOWN")
    po_number = _find_pattern(text, r"Associated\s*PO:\s*([A-Za-z0-9-]+)", default="UNKNOWN")
    bol_number = _find_pattern(text, r"(?:Shipper\s*)?BOL\s*(?:Number)?:\s*([A-Za-z0-9-]+)")
    pro_number = _find_pattern(text, r"Carrier\s*PRO\s*Number:\s*([A-Za-z0-9-]+)")
    invoice_date = _find_pattern(text, r"Billing\s*Date:\s*([\d-]+)", default="2026-09-01")
    bill_to_name = _find_pattern(text, r"Bill\s*To\s*Name:\s*(.+)")
    bill_to_address = _find_pattern(text, r"Bill\s*To\s*Address:\s*(.+)")
    remit_to_name = _find_pattern(text, r"Remit\s*To\s*Name:\s*(.+)")
    remit_to_address = _find_pattern(text, r"Remit\s*To\s*Address:\s*(.+)")
    payment_terms = _find_pattern(text, r"Payment\s*Terms:\s*(.+)")
    payment_due_date = _find_pattern(text, r"Payment\s*Due\s*Date:\s*([\d-]+)")
    if not payment_due_date:
        terms_days = re.fullmatch(r"\s*Net\s+(\d+)\s*", payment_terms, flags=re.IGNORECASE)
        if terms_days:
            payment_due_date = (
                date.fromisoformat(invoice_date)
                + timedelta(days=int(terms_days.group(1)))
            ).isoformat()
    pickup_date = _find_pattern(text, r"Pickup\s*Date:\s*([\d-]+)")
    delivery_date = _find_pattern(text, r"Delivery\s*Date:\s*([\d-]+)")
    freight_class = _find_pattern(text, r"Freight\s*Class:\s*(.+)")
    nmfc_code = _find_pattern(text, r"NMFC\s*Code:\s*([A-Za-z0-9-]+)")
    rate_tariff_reference = _find_pattern(
        text, r"Rate\s*Tariff\s*Reference:\s*([A-Za-z0-9-]+)"
    )
    rate_match = re.search(
        r"Rate\s*Per\s*CWT:\s*\$?\s*([\d,]+(?:\.\d+)?)",
        text,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    rate_per_cwt = _clean_number(rate_match.group(1)) if rate_match else None
    handling_unit_count_value = _find_pattern(
        text, r"Handling\s*Unit\s*Count:\s*(\d+)"
    )
    handling_unit_count = (
        int(handling_unit_count_value) if handling_unit_count_value else None
    )
    handling_unit_type = _find_pattern(text, r"Handling\s*Unit\s*Type:\s*(.+)")

    carrier_name_match = re.search(r"FREIGHT BILLING INVOICE:\s*([A-Za-z0-9& .-]+)", text, flags=re.IGNORECASE)
    carrier_name = carrier_name_match.group(1).strip() if carrier_name_match else "UNKNOWN"

    if not carrier_name or carrier_name == "UNKNOWN":
        lower_text = text.lower()
        if "northstar" in lower_text:
            carrier_name = "Northstar Freight"
        elif "apex" in lower_text:
            carrier_name = "Apex Logistics"
        else:
            carrier_name = "UNKNOWN"

    accessorial_items = [
        {
            "code": code,
            "description": description.strip(),
            "amount": _clean_number(amount),
        }
        for code, description, amount in re.findall(
            r"Accessorial Item:\s*([A-Za-z0-9_-]+)\s*\|\s*(.*?)\s*\|\s*\$?\s*([\d,]+(?:\.\d+)?)",
            text,
            flags=re.IGNORECASE | re.MULTILINE,
        )
    ]
    accessorial_total = sum(item["amount"] for item in accessorial_items)
    if not accessorial_items:
        accessorial_total = _extract_money(
            text, r"Accessorial Surcharges:\s*\$?\s*([\d,]+(?:\.\d+)?)"
        )
    billed_weight = _extract_weight(
        text,
        r"(?:Billed\s*Chargeable\s*Weight|Certified\s*Scale\s*Weight):\s*"
        r"([\d,]+(?:\.\d+)?)\s*(lbs?|kg)?",
    )
    base_rate = _extract_money(
        text,
        r"(?:Base\s*Rate|Base\s*Freight\s*Linehaul):\s*\$?\s*([\d,]+(?:\.\d+)?)",
    )
    fuel_surcharge = _extract_money(
        text,
        r"(?:Fuel\s*Surcharge|Fuel\s*Surcharge\s*Index):\s*\$?\s*([\d,]+(?:\.\d+)?)",
    )
    total_due = _extract_money(
        text,
        r"(?:TOTAL\s*CHARGES\s*DUE|Total\s*Due):\s*\$?\s*([\d,]+(?:\.\d+)?)",
        default=base_rate + fuel_surcharge + accessorial_total,
    )

    return {
        "invoice_number": invoice_number,
        "po_number": po_number,
        "bol_number": bol_number,
        "pro_number": pro_number,
        "carrier_name": carrier_name,
        "billed_chargeable_weight": billed_weight,
        "billed_weight_lbs": billed_weight,
        "billed_base_freight": base_rate,
        "base_rate": base_rate,
        "billed_fuel_surcharge": fuel_surcharge,
        "fuel_surcharge": fuel_surcharge,
        "accessorial_items": accessorial_items,
        "billed_accessorial_fee": accessorial_total,
        "invoice_total_due": total_due,
        "total_amount": total_due,
        "invoice_date": invoice_date,
        "bill_to_name": bill_to_name,
        "bill_to_address": bill_to_address,
        "remit_to_name": remit_to_name,
        "remit_to_address": remit_to_address,
        "payment_terms": payment_terms,
        "payment_due_date": payment_due_date,
        "pickup_date": pickup_date,
        "delivery_date": delivery_date,
        "freight_class": freight_class,
        "nmfc_code": nmfc_code,
        "rate_tariff_reference": rate_tariff_reference,
        "rate_per_cwt": rate_per_cwt,
        "handling_unit_count": int(handling_unit_count),
        "handling_unit_type": handling_unit_type,
    }


def parse_supabase_invoice_pdf(pdf_path: str | os.PathLike[str]) -> dict[str, object]:
    """Map a parsed invoice to the carrier_invoices table schema."""
    parsed = parse_carrier_pdf(pdf_path)
    return {
        "invoice_id": parsed["invoice_number"],
        "invoice_number": parsed["invoice_number"],
        "carrier_name": parsed["carrier_name"],
        "issue_date": parsed["invoice_date"],
        "invoice_date": parsed["invoice_date"],
        "shipment_id": parsed["po_number"],
        "po_number": parsed["po_number"],
        "bol_number": parsed["bol_number"],
        "pro_number": parsed["pro_number"],
        "billed_chargeable_weight": parsed["billed_chargeable_weight"],
        "billed_weight": parsed["billed_weight_lbs"],
        "billed_weight_lbs": parsed["billed_weight_lbs"],
        "invoice_total_due": parsed["invoice_total_due"],
        "base_rate": parsed["billed_base_freight"],
        "billed_base_freight": parsed["billed_base_freight"],
        "fuel_surcharge": parsed["billed_fuel_surcharge"],
        "billed_fuel_surcharge": parsed["billed_fuel_surcharge"],
        "accessorial_items": parsed["accessorial_items"],
        "accessorial_fees": parsed["billed_accessorial_fee"],
        "billed_accessorial_fee": parsed["billed_accessorial_fee"],
        "bill_to_name": parsed["bill_to_name"],
        "bill_to_address": parsed["bill_to_address"],
        "remit_to_name": parsed["remit_to_name"],
        "remit_to_address": parsed["remit_to_address"],
        "payment_terms": parsed["payment_terms"],
        "payment_due_date": parsed["payment_due_date"],
        "pickup_date": parsed["pickup_date"],
        "delivery_date": parsed["delivery_date"],
        "freight_class": parsed["freight_class"],
        "nmfc_code": parsed["nmfc_code"],
        "rate_tariff_reference": parsed["rate_tariff_reference"],
        "rate_per_cwt": parsed["rate_per_cwt"],
        "handling_unit_count": parsed["handling_unit_count"],
        "handling_unit_type": parsed["handling_unit_type"],
    }


def _create_supabase_client():
    """Create a Supabase client from the local environment or .env file."""
    from dotenv import load_dotenv

    load_dotenv()
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_KEY")
    if not supabase_url or not supabase_key:
        raise ValueError("SUPABASE_URL and SUPABASE_KEY must be set")

    from supabase import create_client

    return create_client(supabase_url, supabase_key)


def _invoice_number_key(value: object) -> str:
    return str(value or "").strip().casefold()


def _legacy_invoice_matches(
    existing: dict[str, object],
    incoming: dict[str, object],
) -> bool:
    for column in ("invoice_number", "po_number", "carrier_name", "bol_number"):
        existing_value = str(existing.get(column) or "").strip().casefold()
        incoming_value = str(incoming.get(column) or "").strip().casefold()
        if existing_value != incoming_value:
            return False

    for column in (
        "billed_base_freight",
        "billed_fuel_surcharge",
        "billed_accessorial_fee",
        "billed_weight_lbs",
        "invoice_total_due",
    ):
        try:
            existing_value = float(str(existing.get(column)))
            incoming_value = float(str(incoming.get(column)))
        except (TypeError, ValueError):
            return False
        if not math.isclose(existing_value, incoming_value, rel_tol=0, abs_tol=0.005):
            return False
    return True


def _json_value(value: object) -> JSONValue:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Invoice data contains a non-finite numeric value.")
        return value
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    raise TypeError(f"Invoice data contains a non-JSON value: {type(value).__name__}")


def _json_record(record: dict[str, object]) -> dict[str, JSONValue]:
    return {key: _json_value(value) for key, value in record.items()}


def sync_inbound_pdfs_to_supabase(inbound_dir: str) -> dict[str, object]:
    """Extract PDFs, preserving distinct re-bills while keeping each import idempotent."""
    source_dir = Path(inbound_dir)
    if not source_dir.is_dir():
        return {"processed_count": 0, "processed_files": [], "failed_files": []}

    pdf_paths = sorted(source_dir.glob("*.pdf"))
    if not pdf_paths:
        return {"processed_count": 0, "processed_files": [], "failed_files": []}

    records: list[dict[str, object]] = []
    processed_files: list[str] = []
    failed_files: list[dict[str, str]] = []
    for pdf_path in pdf_paths:
        try:
            record = parse_supabase_invoice_pdf(pdf_path)
            record["source_document_hash"] = hashlib.sha256(
                pdf_path.read_bytes()
            ).hexdigest()
            records.append(record)
            processed_files.append(pdf_path.name)
        except Exception as error:
            failed_files.append({"file": pdf_path.name, "error": str(error)})

    if records:
        client = _create_supabase_client()
        invoice_numbers = list(
            dict.fromkeys(
                str(record["invoice_number"])
                for record in records
                if record.get("invoice_number")
            )
        )
        existing_response = (
            client.table("carrier_invoices")
            .select(
                "invoice_id,invoice_number,source_document_hash,po_number,carrier_name,"
                "bol_number,billed_base_freight,billed_fuel_surcharge,"
                "billed_accessorial_fee,billed_weight_lbs,invoice_total_due"
            )
            .in_("invoice_number", invoice_numbers)
            .execute()
        )
        existing_data = existing_response.data
        if existing_data is not None and not isinstance(existing_data, list):
            raise ValueError("Supabase returned an invalid invoice lookup result.")
        existing_rows: list[dict[str, object]] = []
        for existing in existing_data or []:
            if not isinstance(existing, dict):
                raise ValueError("Supabase returned a non-object invoice lookup row.")
            existing_rows.append(
                {str(column): value for column, value in existing.items()}
            )
        known_by_number: dict[str, list[dict[str, object]]] = {}
        for existing in existing_rows:
            key = _invoice_number_key(existing.get("invoice_number"))
            known_by_number.setdefault(key, []).append(existing)

        for record in records:
            number_key = _invoice_number_key(record.get("invoice_number"))
            source_hash = str(record["source_document_hash"])
            known_rows = known_by_number.setdefault(number_key, [])
            same_document = next(
                (
                    existing
                    for existing in known_rows
                    if existing.get("source_document_hash") == source_hash
                ),
                None,
            )
            if same_document is not None:
                existing_invoice_id = same_document.get("invoice_id")
                if not isinstance(existing_invoice_id, str):
                    raise ValueError("Supabase returned an invoice without a valid invoice ID.")
                record["invoice_id"] = existing_invoice_id
                continue

            legacy_match = next(
                (
                    existing
                    for existing in known_rows
                    if not existing.get("source_document_hash")
                    and _legacy_invoice_matches(existing, record)
                ),
                None,
            )
            if legacy_match is not None:
                existing_invoice_id = legacy_match.get("invoice_id")
                if not isinstance(existing_invoice_id, str):
                    raise ValueError("Supabase returned an invoice without a valid invoice ID.")
                record["invoice_id"] = existing_invoice_id
                legacy_match["source_document_hash"] = source_hash
                continue

            if known_rows:
                record["invoice_id"] = (
                    f"{record['invoice_number']}-DUP-{source_hash}"
                )
            known_rows.append(
                {
                    **record,
                    "invoice_id": record["invoice_id"],
                    "source_document_hash": source_hash,
                }
            )

        records_by_id = {str(record["invoice_id"]): record for record in records}
        json_records = [_json_record(record) for record in records_by_id.values()]
        client.table("carrier_invoices").upsert(
            json_records, on_conflict="invoice_id"
        ).execute()

    return {
        "processed_count": len(processed_files),
        "processed_files": processed_files,
        "failed_files": failed_files,
    }


def update_carrier_staging(pdf_path: str | os.PathLike[str], target_csv: str | os.PathLike[str] = "data/carrier_invoices_raw.csv") -> dict[str, object]:
    """Parse a single inbound invoice PDF and append it to the canonical raw invoice CSV."""
    parsed_record = parse_carrier_pdf(pdf_path)
    destination = Path(target_csv)
    destination.parent.mkdir(parents=True, exist_ok=True)

    if destination.exists():
        existing_df = pd.read_csv(destination)
        existing_df = existing_df[existing_df["invoice_number"].astype(str) != str(parsed_record["invoice_number"])]
        combined_df = pd.concat([existing_df, pd.DataFrame([parsed_record])], ignore_index=True)
    else:
        combined_df = pd.DataFrame([parsed_record])

    combined_df = combined_df.reindex(columns=EXPECTED_COLUMNS)
    combined_df.to_csv(destination, index=False)
    return parsed_record


def sync_inbound_pdfs(inbound_dir: str | os.PathLike[str] = "data/inbound_outlook_invoices", target_csv: str | os.PathLike[str] = "data/carrier_invoices_raw.csv") -> list[dict[str, object]]:
    """Parse every PDF in an inbound Outlook drop folder and sync them to the raw invoice CSV."""
    source_dir = Path(inbound_dir)
    if not source_dir.exists():
        return []

    synced: list[dict[str, object]] = []
    for pdf_path in sorted(source_dir.glob("*.pdf")):
        record = update_carrier_staging(pdf_path, target_csv=target_csv)
        synced.append(record)
    return synced


if __name__ == "__main__":
    synced = sync_inbound_pdfs()
    print(f"Synced {len(synced)} PDF invoice(s) into data/carrier_invoices_raw.csv")

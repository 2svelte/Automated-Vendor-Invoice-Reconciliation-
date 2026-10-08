from __future__ import annotations

import os
import re
from pathlib import Path

import pandas as pd
import pdfplumber

EXPECTED_COLUMNS = [
    "invoice_number",
    "po_number",
    "carrier_name",
    "billed_base_freight",
    "billed_fuel_surcharge",
    "billed_accessorial_fee",
    "billed_weight_lbs",
    "invoice_date",
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
    return _clean_number(match.group(1).replace("lbs", "").strip())


def parse_carrier_pdf(pdf_path: str | os.PathLike[str]) -> dict[str, object]:
    """Parse a carrier PDF into the same schema expected by the reconciliation engine."""
    pdf_file = Path(pdf_path)
    with pdfplumber.open(str(pdf_file)) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)

    invoice_number = _find_pattern(text, r"Invoice\s*Number:\s*([A-Za-z0-9-]+)", default="UNKNOWN")
    po_number = _find_pattern(text, r"Associated\s*PO:\s*([A-Za-z0-9-]+)", default="UNKNOWN")
    invoice_date = _find_pattern(text, r"Billing\s*Date:\s*([\d-]+)", default="2026-09-01")

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

    return {
        "invoice_number": invoice_number,
        "po_number": po_number,
        "carrier_name": carrier_name,
        "billed_base_freight": _extract_money(text, r"Base Freight Linehaul:\s*\$?\s*([\d,]+(?:\.\d+)?)"),
        "billed_fuel_surcharge": _extract_money(text, r"Fuel Surcharge Index:\s*\$?\s*([\d,]+(?:\.\d+)?)"),
        "billed_accessorial_fee": _extract_money(text, r"Accessorial Surcharges:\s*\$?\s*([\d,]+(?:\.\d+)?)"),
        "billed_weight_lbs": _extract_weight(text, r"Certified Scale Weight:\s*([\d,]+)"),
        "invoice_date": invoice_date,
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
        "billed_weight": parsed["billed_weight_lbs"],
        "billed_weight_lbs": parsed["billed_weight_lbs"],
        "base_rate": parsed["billed_base_freight"],
        "billed_base_freight": parsed["billed_base_freight"],
        "fuel_surcharge": parsed["billed_fuel_surcharge"],
        "billed_fuel_surcharge": parsed["billed_fuel_surcharge"],
        "accessorial_fees": parsed["billed_accessorial_fee"],
        "billed_accessorial_fee": parsed["billed_accessorial_fee"],
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


def sync_pdf_to_supabase(pdf_path: str | os.PathLike[str]) -> dict[str, object]:
    """Parse and upsert one invoice PDF into Supabase."""
    path = Path(pdf_path)
    record = parse_supabase_invoice_pdf(path)
    client = _create_supabase_client()
    client.table("carrier_invoices").upsert([record], on_conflict="invoice_id").execute()
    return {"invoice_id": record["invoice_id"], "file": path.name}


def sync_inbound_pdfs_to_supabase(inbound_dir: str) -> dict[str, object]:
    """Extract inbound PDFs and upsert them into Supabase by invoice ID."""
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
            records.append(parse_supabase_invoice_pdf(pdf_path))
            processed_files.append(pdf_path.name)
        except Exception as error:
            failed_files.append({"file": pdf_path.name, "error": str(error)})

    if records:
        client = _create_supabase_client()
        client.table("carrier_invoices").upsert(records, on_conflict="invoice_id").execute()

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

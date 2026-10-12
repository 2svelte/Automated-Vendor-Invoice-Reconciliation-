"""Seed Supabase tables from the repository's demo CSV files."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from src.supabase_data import PROJECT_ROOT, create_supabase_client


def _records_from_csv(path: Path) -> list[dict[str, object]]:
    frame = pd.read_csv(path)
    return frame.astype(object).where(pd.notna(frame), None).to_dict(orient="records")


def _decode_json_columns(
    records: list[dict[str, object]],
    columns: set[str],
) -> list[dict[str, object]]:
    for record in records:
        for column in columns:
            value = record.get(column)
            if isinstance(value, str):
                decoded = json.loads(value)
                if not isinstance(decoded, list):
                    raise ValueError(f"{column} must contain a JSON array.")
                record[column] = decoded
    return records


def _upsert_records(client, table_name: str, records: list[dict[str, object]], key: str) -> int:
    for start in range(0, len(records), 500):
        client.table(table_name).upsert(records[start : start + 500], on_conflict=key).execute()
    return len(records)


def seed_supabase_from_csv(data_dir: str | Path = PROJECT_ROOT / "data") -> dict[str, int]:
    """Upsert local demo PO, invoice, and receipt CSV rows into Supabase."""
    source_dir = Path(data_dir)
    purchase_orders = _decode_json_columns(
        _records_from_csv(source_dir / "erp_purchase_orders.csv"),
        {"approved_accessorial_items", "authorized_accessorial_codes"},
    )
    invoice_source = _decode_json_columns(
        _records_from_csv(source_dir / "carrier_invoices_raw.csv"),
        {"accessorial_items"},
    )
    receipts = _records_from_csv(source_dir / "warehouse_goods_receipts.csv")

    invoices = []
    for record in invoice_source:
        billed_base = record.get("billed_base_freight") or 0.0
        billed_fuel = record.get("billed_fuel_surcharge") or 0.0
        billed_accessorial = record.get("billed_accessorial_fee") or 0.0
        invoices.append(
            {
                **record,
                "invoice_id": record["invoice_number"],
                "issue_date": record["invoice_date"],
                "shipment_id": record["po_number"],
                "bol_number": record.get("bol_number"),
                "pro_number": record.get("pro_number"),
                "billed_chargeable_weight": record.get(
                    "billed_chargeable_weight", record.get("billed_weight_lbs")
                ),
                "accessorial_items": record.get("accessorial_items") or [],
                "billed_weight": record["billed_weight_lbs"],
                "base_rate": record["billed_base_freight"],
                "fuel_surcharge": record["billed_fuel_surcharge"],
                "accessorial_fees": billed_accessorial,
                "invoice_total_due": record.get(
                    "invoice_total_due",
                    billed_base + billed_fuel + billed_accessorial,
                ),
            }
        )

    client = create_supabase_client()
    return {
        "purchase_orders": _upsert_records(client, "purchase_orders", purchase_orders, "po_number"),
        "carrier_invoices": _upsert_records(client, "carrier_invoices", invoices, "invoice_id"),
        "warehouse_goods_receipts": _upsert_records(
            client, "warehouse_goods_receipts", receipts, "po_number"
        ),
    }


def main() -> None:
    counts = seed_supabase_from_csv()
    for table_name, count in counts.items():
        print(f"Upserted {count} row(s) into {table_name}")


if __name__ == "__main__":
    main()
"""Generate deterministic sample PO, carrier invoice, and receipt data."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def generate_mock_data(output_dir: str | Path = DEFAULT_DATA_DIR, seed: int = 7) -> dict[str, Path]:
    """Write realistic demo data and return the generated file paths."""
    del seed  # Records are intentionally explicit so the demo remains stable.
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    purchase_orders = pd.DataFrame(
        [
            ["PO-1001", "V-101", "Northstar Freight", "Chicago, IL", "Columbus, OH", 2450.00, 220.50, 75.00],
            ["PO-1002", "V-102", "BlueLine Logistics", "Dallas, TX", "Phoenix, AZ", 3100.00, 310.00, 100.00],
            ["PO-1003", "V-103", "Atlas Haulage", "Atlanta, GA", "Nashville, TN", 1850.00, 166.50, 60.00],
            ["PO-1004", "V-104", "Northstar Freight", "Seattle, WA", "Portland, OR", 1280.00, 115.20, 50.00],
            ["PO-1005", "V-105", "BlueLine Logistics", "Newark, NJ", "Boston, MA", 2200.00, 198.00, 80.00],
            ["PO-1006", "V-106", "Atlas Haulage", "Memphis, TN", "St. Louis, MO", 1640.00, 147.60, 60.00],
            ["PO-1007", "V-107", "Northstar Freight", "Denver, CO", "Salt Lake City, UT", 2750.00, 247.50, 90.00],
            ["PO-1008", "V-108", "BlueLine Logistics", "Los Angeles, CA", "Las Vegas, NV", 1460.00, 131.40, 55.00],
            ["PO-1009", "V-109", "Atlas Haulage", "Kansas City, MO", "Omaha, NE", 1180.00, 106.20, 45.00],
            ["PO-1010", "V-110", "Northstar Freight", "Cleveland, OH", "Pittsburgh, PA", 1325.00, 119.25, 50.00],
            ["PO-1011", "V-111", "BlueLine Logistics", "Miami, FL", "Orlando, FL", 980.00, 88.20, 40.00],
            ["PO-1012", "V-112", "Atlas Haulage", "Minneapolis, MN", "Madison, WI", 1090.00, 98.10, 45.00],
        ],
        columns=[
            "po_number", "vendor_id", "carrier_name", "origin", "destination",
            "contracted_base_freight", "agreed_fuel_surcharge", "max_accessorial_allowance",
        ],
    )
    approved_accessorials = {
        "PO-1001": [("LFT", "Liftgate Delivery", 75.00)],
        "PO-1002": [("LFT", "Liftgate Delivery", 100.00)],
        "PO-1003": [("LFT", "Liftgate Delivery", 60.00)],
        "PO-1004": [("LFT", "Liftgate Delivery", 50.00)],
        "PO-1005": [("LFT", "Liftgate Delivery", 80.00)],
        "PO-1006": [("LFT", "Liftgate Delivery", 60.00)],
        "PO-1007": [("LFT", "Liftgate Delivery", 90.00)],
        "PO-1008": [("LFT", "Liftgate Delivery", 55.00)],
        "PO-1009": [("LFT", "Liftgate Delivery", 45.00)],
        "PO-1010": [("LFT", "Liftgate Delivery", 50.00)],
        "PO-1011": [("LFT", "Liftgate Delivery", 40.00)],
        "PO-1012": [("LFT", "Liftgate Delivery", 45.00)],
    }
    expected_weights = {
        "PO-1001": 10000, "PO-1002": 12000, "PO-1003": 8000,
        "PO-1004": 6000, "PO-1005": 9000, "PO-1006": 7000,
        "PO-1007": 14000, "PO-1008": 6200, "PO-1009": 5000,
        "PO-1010": 7000, "PO-1011": 4500, "PO-1012": 5200,
    }
    purchase_orders["contracted_expected_weight"] = purchase_orders["po_number"].map(expected_weights)
    purchase_orders["contracted_base_rate"] = purchase_orders["contracted_base_freight"]
    purchase_orders["contracted_fuel_surcharge"] = purchase_orders["agreed_fuel_surcharge"]
    purchase_orders["authorized_carrier"] = purchase_orders["carrier_name"]
    purchase_orders["approved_accessorial_allowance"] = purchase_orders["max_accessorial_allowance"]
    purchase_orders["approved_accessorial_items"] = purchase_orders["po_number"].map(
        lambda po_number: [
            {"code": code, "description": description, "amount": amount}
            for code, description, amount in approved_accessorials[po_number]
        ]
    )
    purchase_orders["authorized_accessorial_codes"] = purchase_orders[
        "approved_accessorial_items"
    ].map(lambda items: [item["code"] for item in items])

    invoices = pd.DataFrame(
        [
            ["INV-1001", "PO-1001", "Northstar Freight", 2450.00, 220.50, 20.00, 10000, "2026-09-01"],
            ["INV-1002", "PO-1002", "BlueLine Logistics", 3100.00, 310.00, 95.00, 12000, "2026-09-02"],
            ["INV-1003", "PO-1003", "Atlas Haulage", 1850.00, 191.50, 60.00, 8000, "2026-09-03"],
            ["INV-1004", "PO-1004", "Northstar Freight", 1280.00, 115.20, 150.00, 6000, "2026-09-04"],
            ["INV-1005", "PO-1005", "BlueLine Logistics", 2200.00, 198.00, 80.00, 9000, "2026-09-05"],
            ["INV-1006", "PO-1006", "Atlas Haulage", 1640.00, 147.60, 60.00, 7000, "2026-09-06"],
            ["INV-1007", "PO-1007", "Northstar Freight", 3180.00, 285.00, 180.00, 15000, "2026-09-07"],
            ["INV-1008", "PO-1008", "BlueLine Logistics", 1460.00, 131.40, 55.00, 6200, "2026-09-08"],
            ["INV-1009", "PO-1009", "Atlas Haulage", 1180.00, 106.20, 45.00, 5000, "2026-09-09"],
            ["INV-1010", "PO-1010", "Northstar Freight", 1350.00, 121.50, 50.00, 7000, "2026-09-10"],
            ["INV-1011", "PO-1011", "BlueLine Logistics", 980.00, 88.20, 40.00, 4500, "2026-09-11"],
            ["INV-1012", "PO-1012", "Atlas Haulage", 1090.00, 98.10, 45.00, 5200, "2026-09-12"],
        ],
        columns=[
            "invoice_number", "po_number", "carrier_name", "billed_base_freight",
            "billed_fuel_surcharge", "billed_accessorial_fee", "billed_weight_lbs", "invoice_date",
        ],
    )
    invoice_accessorials = {
        "INV-1001": [("LFT", "Liftgate Delivery", 20.00)],
        "INV-1002": [("LFT", "Liftgate Delivery", 95.00)],
        "INV-1003": [("LFT", "Liftgate Delivery", 60.00)],
        "INV-1004": [
            ("LFT", "Liftgate Delivery", 60.00),
            ("DET", "Driver Detention", 90.00),
        ],
        "INV-1005": [("LFT", "Liftgate Delivery", 80.00)],
        "INV-1006": [("LFT", "Liftgate Delivery", 60.00)],
        "INV-1007": [
            ("LFT", "Liftgate Delivery", 90.00),
            ("DET", "Driver Detention", 90.00),
        ],
        "INV-1008": [("LFT", "Liftgate Delivery", 55.00)],
        "INV-1009": [("LFT", "Liftgate Delivery", 45.00)],
        "INV-1010": [("LFT", "Liftgate Delivery", 50.00)],
        "INV-1011": [("LFT", "Liftgate Delivery", 40.00)],
        "INV-1012": [("LFT", "Liftgate Delivery", 45.00)],
    }
    invoices["accessorial_items"] = invoices["invoice_number"].map(
        lambda invoice_number: [
            {"code": code, "description": description, "amount": amount}
            for code, description, amount in invoice_accessorials[invoice_number]
        ]
    )
    invoices["billed_accessorial_fee"] = invoices["accessorial_items"].map(
        lambda items: sum(item["amount"] for item in items)
    )
    invoices["billed_chargeable_weight"] = invoices["billed_weight_lbs"]
    invoices["base_rate"] = invoices["billed_base_freight"]
    invoices["fuel_surcharge"] = invoices["billed_fuel_surcharge"]
    invoices["bol_number"] = invoices["po_number"].map(lambda po: f"BOL-{po[3:]}")
    invoices["pro_number"] = invoices["po_number"].map(lambda po: f"PRO-884{po[3:]}")
    invoices["invoice_total_due"] = (
        invoices["billed_base_freight"]
        + invoices["billed_fuel_surcharge"]
        + invoices["billed_accessorial_fee"]
    )

    receipts = pd.DataFrame(
        [
            ["PO-1001", "2026-08-29 14:10", "Maria Chen", 10000],
            ["PO-1002", "2026-08-30 09:30", "James Patel", 12000],
            ["PO-1003", "2026-08-31 16:45", "Avery Brooks", 8000],
            ["PO-1004", "2026-09-01 11:20", "Maria Chen", 6000],
            ["PO-1005", "2026-09-02 13:15", "James Patel", 9000],
            ["PO-1006", "2026-09-03 10:00", "Avery Brooks", 7000],
            ["PO-1007", "2026-09-04 15:35", "Maria Chen", 14000],
            ["PO-1008", "2026-09-05 08:50", "James Patel", 6200],
            ["PO-1009", "2026-09-06 12:25", "Avery Brooks", 5000],
            ["PO-1010", "2026-09-07 09:05", "Maria Chen", 7000],
            ["PO-1011", "2026-09-08 14:40", "James Patel", 4500],
        ],
        columns=["po_number", "delivery_timestamp", "dock_signature", "actual_scale_weight_lbs"],
    )
    receipts["receipt_id"] = receipts["po_number"].map(lambda po: f"GRN-{po[3:]}")
    receipts["bol_number"] = receipts["po_number"].map(lambda po: f"BOL-{po[3:]}")
    receipts["received_timestamp"] = receipts["delivery_timestamp"]
    receipts["scale_ticket_id"] = receipts["receipt_id"].map(lambda receipt: f"ST-{receipt[4:]}")
    receipts["receiving_dock_door"] = receipts["receipt_id"].map(
        lambda receipt: f"BAY-{int(receipt[4:]) % 8 + 1:02d}"
    )
    receipts["trailer_license_plate"] = receipts["receipt_id"].map(
        lambda receipt: f"CO-{int(receipt[4:]):04d}"
    )
    receipts["gross_weight_lbs"] = receipts["actual_scale_weight_lbs"] + 21000
    receipts["tare_weight_lbs"] = 21000
    receipts["certified_net_scale_weight"] = receipts["actual_scale_weight_lbs"]
    receipts["scale_certification_seal"] = "NIST-CERT-2026"
    receipts["osd_notes"] = receipts["po_number"].map(
        lambda po: "1 pallet damaged on arrival" if po == "PO-1005" else ""
    )

    paths = {
        "purchase_orders": output_path / "erp_purchase_orders.csv",
        "invoices": output_path / "carrier_invoices_raw.csv",
        "receipts": output_path / "warehouse_goods_receipts.csv",
        "audit_log": output_path / "reconciled_audit_log.csv",
    }
    for frame, path, json_columns in (
        (purchase_orders, paths["purchase_orders"], {"approved_accessorial_items", "authorized_accessorial_codes"}),
        (invoices, paths["invoices"], {"accessorial_items"}),
        (receipts, paths["receipts"], set()),
    ):
        export_frame = frame.copy()
        for column in json_columns:
            export_frame[column] = export_frame[column].map(json.dumps)
        export_frame.to_csv(path, index=False)
    from src.reconciliation_engine import reconcile

    audit_log = reconcile(purchase_orders, invoices, receipts)
    for column in (
        "accessorial_items",
        "approved_accessorial_items",
        "authorized_accessorial_codes",
        "unauthorized_accessorial_items",
    ):
        if column in audit_log:
            audit_log[column] = audit_log[column].map(
                lambda value: json.dumps(value) if isinstance(value, list) else value
            )
    audit_log.to_csv(paths["audit_log"], index=False)
    return paths


if __name__ == "__main__":
    generated = generate_mock_data()
    for name, path in generated.items():
        print(f"{name}: {path}")

"""Streamlit workbench for exception-based three-way freight invoice audits."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from html import escape
from numbers import Real

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from src.agent import draft_dispute
from src.dispute_packet import generate_dispute_packet_pdf
from src.reconciliation_engine import (
    BASE_PEG_PRICE,
    DISCREPANCY_FLAGGED,
    REVIEW_INCOMPLETE,
    REVIEW_REQUIRED,
    TOUCHLESS_APPROVED,
    UNAUTHORIZED_FUEL_CREEP,
    calculate_authorized_fuel,
    summarize_carrier_billing,
    summarize,
)
from src.supabase_data import (
    create_supabase_client,
    fetch_dispute_dispatches,
    fetch_invoice_audit_events,
    queue_dispute_email,
    reconcile_supabase_data,
    update_invoice_audit,
)

OUTLOOK_FROM_EMAIL = "nadiaravindra07@outlook.com"
LB_TO_KG = 0.45359237
HANDLED_STATUSES = {
    "OVERRIDE_APPROVED",
    "DISPUTED",
    "SCALE_TICKET_REQUESTED",
    "REBUTTAL_RECEIVED",
    "CLOSED_RESOLVED",
    "CLOSED_OVERRIDDEN",
}
AUDIT_STATUS_LABELS = {
    TOUCHLESS_APPROVED: "Touchless Approved",
    REVIEW_REQUIRED: "Review Required",
    DISCREPANCY_FLAGGED: "Discrepancy Flagged",
    REVIEW_INCOMPLETE: "Review Incomplete",
    "DUPLICATE_BILLING_BLOCKED": "Duplicate Billing Blocked",
    "OVERRIDE_APPROVED": "Override Approved",
    "DISPUTED": "Disputed",
    "SCALE_TICKET_REQUESTED": "Scale Ticket Requested",
    "UNDER_REVIEW": "Under Review",
    "REBUTTAL_RECEIVED": "Rebuttal Received",
    "CLOSED_RESOLVED": "Closed Resolved",
    "CLOSED_OVERRIDDEN": "Closed Overridden",
}


@st.cache_resource
def get_supabase_client():
    return create_supabase_client()


def _numeric_value(value: object) -> float | None:
    if not isinstance(value, Real):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def format_money(value: object) -> str:
    number = _numeric_value(value)
    if number is None:
        return "N/A"
    return f"${number:,.2f}"


def _format_aud_money(value: object) -> str:
    number = format_money(value)
    return f"{number} AUD" if number != "N/A" else number


def _format_withheld_amount(value: object) -> str:
    number = _numeric_value(value)
    if number is None:
        return "N/A"
    return f"${max(0.0, number):,.2f} AUD"


def _format_invoice_accuracy(
    accuracy: float, clean_invoices: int, invoice_count: int
) -> str:
    return f"{accuracy:.0%} ({clean_invoices} / {invoice_count} Clean)"


def _dispute_memo_button_label(has_generated: bool) -> str:
    return "Regenerate Dispute Memo" if has_generated else "Generate Dispute Memo"


def _rebuttal_sla(deadline: object, audit_status: object = "") -> str:
    if audit_status == "REBUTTAL_RECEIVED":
        return "Response received"
    if audit_status in {"CLOSED_RESOLVED", "CLOSED_OVERRIDDEN"}:
        return "Closed"
    if deadline is None or not str(deadline).strip():
        return "—"
    parsed_deadline = pd.to_datetime(str(deadline), utc=True, errors="coerce")
    if pd.isna(parsed_deadline):
        return "—"
    days_remaining = (
        parsed_deadline.date() - datetime.now(timezone.utc).date()
    ).days
    if days_remaining < 0:
        return f"{abs(days_remaining)} days overdue"
    if days_remaining == 0:
        return "Due today"
    return f"{days_remaining} days remaining"


def _dispute_packet_filename(invoice_number: str) -> str:
    safe_invoice_number = "".join(
        character
        for character in invoice_number
        if character.isascii() and (character.isalnum() or character in "._-")
    )
    return f"Dispute_Packet_{safe_invoice_number or 'invoice'}.pdf"


def _default_carrier_email(selected: pd.Series) -> str:
    saved_email = selected.get("dispute_to_email")
    if isinstance(saved_email, str) and saved_email.strip():
        return saved_email.strip()
    carrier_email = selected.get("carrier_email")
    return carrier_email.strip() if isinstance(carrier_email, str) else ""


def _leakage_chart_html(leakage_summary: pd.DataFrame) -> str:
    max_amount = float(leakage_summary["Amount"].max()) if not leakage_summary.empty else 0
    rows = []
    for _, row in leakage_summary.iterrows():
        amount = float(row["Amount"])
        bar_width = amount / max_amount * 55 if max_amount > 0 else 0
        label = f"{format_money(amount)} ({float(row['Share']):.1%})"
        rows.append(
            f"""
            <div style="display:flex;align-items:center;gap:0.75rem;margin:0.55rem 0;"
                 role="img" aria-label="{escape(str(row['Leakage category']))}: {escape(label)}">
              <div style="flex:0 0 11rem;color:#fff;font-size:0.9rem;text-shadow:0 1px 2px rgba(0,0,0,0.85);">
                {escape(str(row['Leakage category']))}
              </div>
              <div style="height:1.5rem;flex:1;position:relative;">
                <div style="height:100%;width:{bar_width:.2f}%;background:#3b82f6;border-radius:0.25rem;"></div>
                <div style="position:absolute;left:calc({bar_width:.2f}% + 0.5rem);top:50%;transform:translateY(-50%);color:#fff;font-size:0.9rem;font-weight:600;white-space:nowrap;text-shadow:0 1px 2px rgba(0,0,0,0.85);">
                  {escape(label)}
                </div>
              </div>
            </div>
            """
        )
    return (
        '<div style="width:100%;font-family:\'Source Sans Pro\',sans-serif;font-size:14px;">'
        + "".join(rows)
        + "</div>"
    )


def format_weight(value: object) -> str:
    number = _numeric_value(value)
    if number is None:
        return "N/A"
    return f"{number * LB_TO_KG:,.0f} kg"


def _money_delta(value: object) -> str:
    number = _numeric_value(value)
    if number is None:
        return "N/A"
    if number == 0:
        return "$0.00"
    return f"{'+' if number > 0 else '-'}${abs(number):,.2f}"


def _format_billing_variance(billed: object, authorized: object) -> str:
    billed_amount = _numeric_value(billed)
    authorized_amount = _numeric_value(authorized)
    if billed_amount is None or authorized_amount is None:
        return "N/A"
    variance = billed_amount - authorized_amount
    if variance > 0:
        return f"+${variance:,.2f}"
    if variance < 0:
        return f"-${abs(variance):,.2f}"
    return "$0.00"


def _weight_delta(value: object) -> str:
    number = _numeric_value(value)
    if number is None:
        return "N/A"
    if number == 0:
        return "0 kg"
    return f"{number * LB_TO_KG:+,.0f} kg"


def _primary_root_cause(row: pd.Series) -> str:
    duplicate_of = row.get("duplicate_of_invoice_id")
    if isinstance(duplicate_of, str) and duplicate_of.strip():
        return f"Duplicate bill — previously processed {duplicate_of}"
    if row.get("root_cause") == UNAUTHORIZED_FUEL_CREEP:
        return "Unauthorized Fuel Creep"
    if not bool(row.get("receipt_found")):
        return "Missing Scale Receipt"

    base_variance = _numeric_value(row.get("base_variance")) or 0.0
    fuel_variance = _numeric_value(row.get("fuel_variance")) or 0.0
    unauthorized_items = row.get("unauthorized_accessorial_items", [])
    accessorial_descriptions = sorted(
        {
            str(item.get("description") or item.get("code") or "Accessorial")
            .strip()
            .title()
            for item in unauthorized_items
            if isinstance(item, dict)
        }
    ) if isinstance(unauthorized_items, list) else []

    if accessorial_descriptions:
        accessorial_cause = ", ".join(accessorial_descriptions)
        if base_variance > 0:
            return f"Base + {accessorial_cause}"
        if len(accessorial_descriptions) > 1:
            return f"Unapproved Accessorials: {accessorial_cause}"
        return f"Unapproved {accessorial_cause}"

    if base_variance > 0 and fuel_variance > 0:
        return "Base + Fuel Variance"
    if base_variance > 0:
        return "Base Rate Variance"
    if fuel_variance > 0:
        return "Fuel Index Pegged"
    if (_numeric_value(row.get("weight_variance_lbs")) or 0.0) > 0:
        return "Weight Overweight"
    return "Review Required"


def _display_status(status: object) -> str:
    value = str(status)
    return AUDIT_STATUS_LABELS.get(
        value, value.replace("_", " ").strip().title()
    )


def _workbench_table_html(
    frame: pd.DataFrame,
    *,
    table_class: str,
    column_widths: list[int],
    column_groups: dict[str, list[str]] | None = None,
) -> str:
    columns = list(zip(frame.columns, column_widths, strict=True))
    column_groups = column_groups or {}
    group_by_first_column = {
        members[0]: (label, len(members))
        for label, members in column_groups.items()
    }
    grouped_columns = {
        column for members in column_groups.values() for column in members
    }

    def header_cell(index: int, column: object, attributes: str = "") -> str:
        return (
            f'<th scope="col"{attributes}>{escape(str(column))}'
            + (
                f'<span class="column-resizer" data-column-index="{index}" '
                f'role="separator" tabindex="0" aria-orientation="vertical" '
                f'aria-label="Resize {escape(str(column))} column"></span>'
                if index < len(columns) - 1
                else ""
            )
            + "</th>"
        )

    if column_groups:
        top_cells = []
        sub_cells = []
        for index, (column, _) in enumerate(columns):
            if column in group_by_first_column:
                label, span = group_by_first_column[column]
                top_cells.append(
                    f'<th scope="colgroup" colspan="{span}" '
                    f'class="column-group">{escape(label)}</th>'
                )
            if column in grouped_columns:
                sub_cells.append(header_cell(index, column))
            else:
                top_cells.append(header_cell(index, column, ' rowspan="2"'))
        header = (
            f"<tr>{''.join(top_cells)}</tr><tr>{''.join(sub_cells)}</tr>"
        )
    else:
        header = "<tr>" + "".join(
            header_cell(index, column)
            for index, (column, _) in enumerate(columns)
        ) + "</tr>"
    colgroup = "".join(
        f'<col style="width:{width}%">' for _, width in columns
    )
    rows = []
    for _, row in frame.iterrows():
        component = str(row.get("Component", ""))
        if component in {
            "Financial Line Items (Charges)",
            "Physical Scale / Weight Verification (Compliance & Billing Basis)",
        }:
            rows.append(
                '<tr class="table-tier-heading">'
                f'<td colspan="{len(columns)}">{escape(component)}</td>'
                "</tr>"
            )
            continue

        cells = []
        for column, value in row.items():
            text = str(value)
            classes = []
            if column == "Status":
                classes.append("status-cell")
            elif column == "Primary Root Cause":
                classes.append("root-cause-cell")
            elif column == "Variance" and text.startswith("+"):
                if str(row.get("Component", "")) == "Fuel Surcharge (AIP Weekly Index)":
                    classes.append("variance-fuel-overcharge")
                else:
                    classes.append("variance-flagged")
            elif column == "Variance" and text.startswith("-"):
                classes.append("variance-credit")
            elif column == "Variance" and text == "$0.00":
                classes.append("variance-neutral")
            class_attribute = f' class="{" ".join(classes)}"' if classes else ""
            escaped_text = escape(text)
            is_fuel_variance = (
                column == "Variance"
                and str(row.get("Component", ""))
                == "Fuel Surcharge (AIP Weekly Index)"
            )
            cell_content = (
                f'<span class="variance-overcharge-badge">{escaped_text}</span>'
                if column == "Variance" and text.startswith("+") and not is_fuel_variance
                else escaped_text
            )
            cells.append(
                f"<td{class_attribute} title=\"{escaped_text}\">{cell_content}</td>"
            )
        rows.append(f"<tr>{''.join(cells)}</tr>")
    return (
        """<!doctype html>
        <html>
        <head>
          <style>
            html, body { margin: 0; padding: 0; background: transparent; }
            .workbench-table-wrap {
              width: 100%; overflow-x: auto; margin-bottom: 0.375rem;
            }
            table.workbench-table {
              width: 100%; table-layout: fixed; border-collapse: collapse;
              font: 16px/1.4 "Source Sans Pro", sans-serif;
            }
            table.worklist-table { min-width: 1180px; }
            table.reconciliation-table { min-width: 560px; }
            table.workbench-table th, table.workbench-table td {
              box-sizing: border-box; border: 1px solid #34383f;
              padding: 3px 6px; text-align: left; white-space: nowrap;
              overflow: hidden; text-overflow: ellipsis;
            }
            table.workbench-table th {
              position: relative; padding-right: 12px; background: #15191f;
              color: #f1f3f5; font-weight: 600; white-space: normal;
            }
            table.workbench-table th.column-group {
              text-align: center; padding-right: 6px;
            }
            table.workbench-table td { background: #0e1117; color: #f1f3f5; }
            table.workbench-table .status-cell,
            table.workbench-table .root-cause-cell {
              color: #fff; font-family: inherit; font-size: 16px;
              font-weight: 400;
            }
            table.workbench-table .status-cell { text-align: left; }
            table.workbench-table .root-cause-cell { text-align: left; }
            table.workbench-table tr.table-tier-heading td {
              padding: 7px 8px; background: #202833; color: #d8e5f4;
              font-weight: 700; text-align: left; white-space: normal;
            }
            .column-resizer {
              position: absolute; z-index: 1; top: 0; right: -3px; bottom: 0;
              width: 8px; cursor: col-resize; touch-action: none;
              user-select: none;
            }
            .column-resizer:hover, .column-resizer:focus-visible {
              background: #60a5fa; outline: none;
            }
            table.workbench-table .variance-flagged {
              color: #fff; text-align: right;
            }
            table.workbench-table .variance-fuel-overcharge {
              color: #e46b6b; font-weight: 600; text-align: right;
            }
            .variance-overcharge-badge {
              display: inline-block; padding: 1px 6px; border-radius: 4px;
              background: #c63b40; color: #fff; font-weight: 700;
            }
            table.workbench-table .variance-credit {
              color: #34d399; font-weight: 600; text-align: right;
            }
            table.workbench-table .variance-neutral {
              color: #aab2bd; text-align: right;
            }
            table.reconciliation-table td:not(:first-child) { text-align: right; }
            table.reconciliation-table td:first-child { white-space: normal; }
          </style>
        </head>
        <body>
        """
        + f'<div class="workbench-table-wrap"><table class="workbench-table '
        + f'{escape(table_class)}" data-table-key="{escape(table_class)}">'
        + f"<colgroup>{colgroup}</colgroup>"
        + f"<thead>{header}</thead><tbody>{''.join(rows)}</tbody>"
        + """</table></div>
        <script>
          const table = document.querySelector("table.workbench-table");
          const columns = Array.from(table.querySelectorAll("col"));
          const handles = Array.from(table.querySelectorAll(".column-resizer"));
          const storageKey = `freight-table-widths:${table.dataset.tableKey}`;
          const minimumColumnWidth = 56;

          function applyWidths(widths) {
            if (!Array.isArray(widths) || widths.length !== columns.length ||
                widths.some(width => !Number.isFinite(width) || width <= 0)) {
              return false;
            }
            const total = widths.reduce((sum, width) => sum + width, 0);
            columns.forEach((column, index) => {
              column.style.width = `${widths[index] / total * 100}%`;
            });
            return true;
          }

          try {
            const savedWidths = JSON.parse(localStorage.getItem(storageKey));
            if (savedWidths !== null) applyWidths(savedWidths);
          } catch (error) {
            console.error("Could not restore saved table column widths.", error);
          }

          function saveWidths() {
            const widths = columns.map(column =>
              column.getBoundingClientRect().width
            );
            try {
              localStorage.setItem(storageKey, JSON.stringify(widths));
            } catch (error) {
              console.error("Could not save table column widths.", error);
            }
          }

          function resizeColumn(index, requestedDelta) {
            const tableWidth = table.getBoundingClientRect().width;
            const leftWidth = columns[index].getBoundingClientRect().width;
            const rightWidth = columns[index + 1].getBoundingClientRect().width;
            const delta = Math.max(
              minimumColumnWidth - leftWidth,
              Math.min(requestedDelta, rightWidth - minimumColumnWidth)
            );
            columns[index].style.width = `${(leftWidth + delta) / tableWidth * 100}%`;
            columns[index + 1].style.width =
              `${(rightWidth - delta) / tableWidth * 100}%`;
          }

          handles.forEach(handle => {
            const index = Number(handle.dataset.columnIndex);
            handle.addEventListener("pointerdown", event => {
              event.preventDefault();
              handle.focus();
              const startX = event.clientX;
              const tableWidth = table.getBoundingClientRect().width;
              const leftWidth = columns[index].getBoundingClientRect().width;
              const rightWidth = columns[index + 1].getBoundingClientRect().width;
              const minimumDelta = minimumColumnWidth - leftWidth;
              const maximumDelta = rightWidth - minimumColumnWidth;
              const move = moveEvent => {
                const delta = Math.max(
                  minimumDelta,
                  Math.min(moveEvent.clientX - startX, maximumDelta)
                );
                columns[index].style.width =
                  `${(leftWidth + delta) / tableWidth * 100}%`;
                columns[index + 1].style.width =
                  `${(rightWidth - delta) / tableWidth * 100}%`;
              };
              const finish = () => {
                window.removeEventListener("pointermove", move);
                window.removeEventListener("pointerup", finish);
                saveWidths();
              };
              window.addEventListener("pointermove", move);
              window.addEventListener("pointerup", finish, { once: true });
            });
            handle.addEventListener("keydown", event => {
              if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
              event.preventDefault();
              resizeColumn(index, event.key === "ArrowLeft" ? -12 : 12);
              saveWidths();
            });
          });
        </script>
        </body>
        </html>
        """
    )


def _table_component_height(row_count: int, header_rows: int = 1) -> int:
    return max(96, (row_count + header_rows) * 32 + 6)


def _comparison_table(row: pd.Series) -> pd.DataFrame:
    billed_weight = _numeric_value(
        row.get("billed_chargeable_weight", row.get("billed_weight_lbs"))
    )
    scale_weight = _numeric_value(
        row.get("certified_net_scale_weight", row.get("actual_scale_weight_lbs"))
    )
    expected_weight = _numeric_value(
        row.get("contracted_expected_weight", row.get("expected_weight"))
    )
    base_freight = _numeric_value(
        row.get("contracted_base_rate", row.get("contracted_base_freight"))
    )
    shipment_date = row.get(
        "fuel_shipment_date",
        row.get("pickup_date", row.get("invoice_date", "")),
    )
    fuel_authorization = (
        calculate_authorized_fuel(base_freight, str(shipment_date))
        if base_freight is not None
        else None
    )
    result = [
        {
            "Component": "Financial Line Items (Charges)",
            "Invoiced (Carrier)": "",
            "Authorized (PO)": "",
            "Physical (WMS)": "",
            "Variance": "",
        },
        {
            "Component": "Base freight",
            "Invoiced (Carrier)": _format_aud_money(row.get("billed_base_freight")),
            "Authorized (PO)": _format_aud_money(
                row.get("contracted_base_rate", row.get("contracted_base_freight"))
            ),
            "Physical (WMS)": "—",
            "Variance": _format_billing_variance(
                row.get("billed_base_freight"),
                row.get("contracted_base_rate", row.get("contracted_base_freight")),
            ),
        },
        {
            "Component": "Fuel Surcharge (AIP Weekly Index)",
            "Invoiced (Carrier)": _format_aud_money(
                row.get("billed_fuel_surcharge")
            ),
            "Authorized (PO)": _format_aud_money(
                fuel_authorization["authorized_amount"]
                if fuel_authorization is not None
                else row.get("authorized_fuel_surcharge")
            ),
            "Physical (WMS)": "—",
            "Variance": _format_billing_variance(
                row.get("billed_fuel_surcharge"),
                fuel_authorization["authorized_amount"]
                if fuel_authorization is not None
                else row.get("authorized_fuel_surcharge"),
            ),
        },
    ]

    result.append(
        {
            "Component": "Accessorials",
            "Invoiced (Carrier)": "",
            "Authorized (PO)": "",
            "Physical (WMS)": "",
            "Variance": "",
        }
    )
    billed_items = row.get("accessorial_items", [])
    approved_items = row.get("approved_accessorial_items", [])
    if isinstance(billed_items, list) and billed_items:
        approved_by_code: dict[str, float] = {}
        if isinstance(approved_items, list):
            for item in approved_items:
                code = str(item.get("code", "")).strip().upper()
                amount = _numeric_value(item.get("amount")) or 0.0
                approved_by_code[code] = approved_by_code.get(code, 0.0) + amount
        billed_by_code: dict[str, dict[str, object]] = {}
        for item in billed_items:
            code = str(item.get("code", "")).strip().upper()
            entry = billed_by_code.setdefault(
                code,
                {"description": str(item.get("description", "")).strip(), "amount": 0.0},
            )
            entry["amount"] = float(entry["amount"]) + (
                _numeric_value(item.get("amount")) or 0.0
            )
        for code, item in billed_by_code.items():
            billed_amount = float(item["amount"])
            authorized_amount = approved_by_code.get(code, 0.0)
            result.append(
                {
                    "Component": (
                        f"\u00a0\u00a0• {code or 'UNKNOWN'}: "
                        f"{item['description']}"
                    ),
                    "Invoiced (Carrier)": _format_aud_money(billed_amount),
                    "Authorized (PO)": _format_aud_money(authorized_amount),
                    "Physical (WMS)": "—",
                    "Variance": _format_billing_variance(
                        billed_amount, authorized_amount
                    ),
                }
            )
    else:
        result.append(
            {
                "Component": "\u00a0\u00a0• Accessorial fees (legacy aggregate)",
                "Invoiced (Carrier)": _format_aud_money(
                    row.get("billed_accessorial_fee")
                ),
                "Authorized (PO)": _format_aud_money(
                    row.get("max_accessorial_allowance")
                ),
                "Physical (WMS)": "—",
                "Variance": _format_billing_variance(
                    row.get("billed_accessorial_fee"),
                    row.get("max_accessorial_allowance"),
                ),
            }
        )

    weight_difference = billed_weight - scale_weight if (
        billed_weight is not None and scale_weight is not None
    ) else None
    weight_variance_label = _weight_delta(weight_difference)
    result.extend(
        [
            {
                "Component": (
                    "Physical Scale / Weight Verification "
                    "(Compliance & Billing Basis)"
                ),
                "Invoiced (Carrier)": "",
                "Authorized (PO)": "",
                "Physical (WMS)": "",
                "Variance": "",
            },
            {
                "Component": "Certified Net Chargeable Weight",
                "Invoiced (Carrier)": format_weight(billed_weight),
                "Authorized (PO)": format_weight(expected_weight),
                "Physical (WMS)": format_weight(scale_weight),
                "Variance": weight_variance_label,
            },
            {
                "Component": "\u00a0\u00a0• Gross Vehicle Mass (GVM)",
                "Invoiced (Carrier)": "—",
                "Authorized (PO)": "—",
                "Physical (WMS)": format_weight(row.get("gross_weight_lbs")),
                "Variance": "—",
            },
            {
                "Component": "\u00a0\u00a0• Empty Truck + Trailer Tare Weight",
                "Invoiced (Carrier)": "—",
                "Authorized (PO)": "—",
                "Physical (WMS)": format_weight(row.get("tare_weight_lbs")),
                "Variance": "—",
            },
        ]
    )
    return pd.DataFrame(result)


def _render_fuel_audit_breakdown(
    row: pd.Series, fuel_tolerance: float
) -> None:
    base_freight = _numeric_value(
        row.get("contracted_base_rate", row.get("contracted_base_freight"))
    )
    billed_fuel = _numeric_value(row.get("billed_fuel_surcharge"))
    shipment_date = row.get(
        "fuel_shipment_date",
        row.get("pickup_date", row.get("invoice_date", "")),
    )
    if base_freight is None or billed_fuel is None:
        st.warning("AIP fuel audit details are unavailable for this invoice.")
        return

    authorization = calculate_authorized_fuel(base_freight, str(shipment_date))
    billed_pct = billed_fuel / base_freight if base_freight else 0.0
    fuel_overcharge = max(0.0, billed_fuel - authorization["authorized_amount"])
    parsed_date = pd.to_datetime(shipment_date, errors="coerce")
    week_start = parsed_date - pd.Timedelta(days=parsed_date.weekday())
    index_price_label = (
        f"Price for Week of {week_start.strftime('%d %b %Y')}"
        if not pd.isna(parsed_date)
        else "Price for Shipment Week (unavailable)"
    )

    with st.container(border=True, key="aip_fuel_audit_card"):
        st.markdown(
            """
            <style>
            .st-key-aip_fuel_audit_card [data-testid="stColumn"]
            [data-testid="stVerticalBlock"] {
                gap: 0.5rem;
            }
            </style>
            """,
            unsafe_allow_html=True,
        )
        st.markdown("#### AIP Diesel Fuel Surcharge Audit Breakdown")
        details = st.columns([1.1, 1.1, 1.5])
        details[0].caption(index_price_label)
        details[0].markdown(
            f"**${authorization['index_price']:.2f} AUD / L**"
        )
        details[1].caption("Contract Base Peg")
        details[1].markdown(f"**${BASE_PEG_PRICE:.2f} AUD / L**")
        details[2].markdown(
            f"Billed: {billed_pct:.2%} "
            f"({_format_aud_money(billed_fuel)})"
        )
        details[2].markdown(
            f"Authorized: {authorization['allowed_pct']:.2%} "
            f"({_format_aud_money(authorization['authorized_amount'])})"
        )
        discrepancy = _format_billing_variance(
            billed_fuel, authorization["authorized_amount"]
        )
        if fuel_overcharge > fuel_tolerance:
            details[2].error(f"Overcharge: {discrepancy} AUD")
        elif fuel_overcharge > 0:
            details[2].caption(
                f"Within AIP fuel tolerance: {discrepancy} AUD"
            )
        elif billed_fuel < authorization["authorized_amount"]:
            details[2].caption(f"Favorable savings: {discrepancy} AUD")
        else:
            details[2].caption(f"Variance: {discrepancy} AUD")


def _save_resolution(
    client,
    invoice_id: str,
    invoice_number: str,
    audit_status: str,
    audit_note: str,
) -> None:
    try:
        update_invoice_audit(client, invoice_id, audit_status, audit_note)
    except Exception as error:
        st.error(f"Could not save the resolution for {invoice_number}: {error}")
        return
    st.success(f"{invoice_number} updated: {AUDIT_STATUS_LABELS[audit_status]}.")
    st.rerun()


st.set_page_config(page_title="Freight Audit Workbench", page_icon="FA", layout="wide")
st.title("Freight Reconciliation & Exception Management")
st.markdown(
    """
    <style>
    [data-testid="stMainBlockContainer"] {
        padding-top: 48px !important;
    }
    [data-testid="stMetricLabel"] p {
        font-size: 20px !important;
    }
    [data-testid="stMarkdownContainer"] p,
    [data-testid="stCaptionContainer"] p,
    [data-testid="stMultiSelectTagsContainer"] span,
    [data-testid="stBaseButton-header"] span {
        font-size: 20px !important;
    }
    [data-testid="stMarkdownContainer"] h3 {
        font-size: 18px !important;
    }
    [data-testid="stMarkdownContainer"] h4 {
        font-size: 16px !important;
    }
    [data-testid="stSidebar"] [data-testid="stSlider"] [data-orientation="horizontal"] > [data-rac] > [data-rac] {
        background-color: #1e3a8a !important;
    }
    [data-testid="stSidebar"] [data-testid="stSlider"] [data-orientation="horizontal"] > [data-rac] > div:first-child {
        filter: hue-rotate(240deg) brightness(0.55) !important;
    }
    [data-testid="stSidebar"] [data-testid="stSlider"] [data-testid="stSliderThumbValue"] {
        background: transparent !important;
        border: 0 !important;
        color: #f1f3f5 !important;
    }
    [data-testid="stSidebar"] [data-testid="stSlider"] [data-testid="stSliderThumbValue"] p {
        color: #f1f3f5 !important;
    }
    .st-key-worklist_view [data-testid="stRadioOption"][data-selected="true"] > div > div:first-child,
    .st-key-exception_filter [data-testid="stRadioOption"][data-selected="true"] > div > div:first-child {
        background-color: #1e3a8a !important;
        border-color: #1e3a8a !important;
    }
    .st-key-carrier_selection [data-testid="stMultiSelect"] [data-tag] {
        background-color: #1e3a8a !important;
        border-color: #1e3a8a !important;
    }
    .st-key-carrier_selection [data-testid="stMultiSelect"] [role="group"][data-rac]:focus-within {
        border-color: #1e3a8a !important;
        box-shadow: 0 0 0 1px #1e3a8a !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

with st.sidebar:
    st.subheader("Audit controls")
    fuel_tolerance = st.slider(
        "AIP Fuel Tolerance ($ AUD)",
        min_value=0.0,
        max_value=25.0,
        value=1.0,
        step=0.5,
        help=(
            "Defines allowable rounding tolerance against the deterministic "
            "AIP benchmark formula before triggering an overcharge exception."
        ),
    )
    weight_tolerance_pct = st.slider(
        "Weight buffer (%)",
        min_value=0.0,
        max_value=10.0,
        value=2.0,
        step=0.25,
        help="Carrier weight may exceed the certified scale weight by this percentage.",
    )


@st.fragment(run_every="15s")
def render_audit() -> None:
    try:
        client = get_supabase_client()
        audited = reconcile_supabase_data(
            client,
            fuel_tolerance=fuel_tolerance,
            weight_tolerance_pct=weight_tolerance_pct,
        )
        dispatches = pd.DataFrame(fetch_dispute_dispatches(client))
    except Exception as error:
        st.error(
            "Could not load and audit Supabase data, including the dispute "
            f"dispatch queue: {error}"
        )
        return

    if not dispatches.empty:
        latest_dispatches = (
            dispatches.drop_duplicates("invoice_id", keep="first")
            .drop(columns="queued_at")
        )
        audited = audited.merge(latest_dispatches, on="invoice_id", how="left")
    else:
        audited["dispatch_id"] = None
        audited["dispute_to_email"] = None
        audited["email_dispatch_status"] = None
        audited["dispatched_at"] = None
        audited["error_message"] = None

    success_message = st.session_state.pop("dispatch_success_message", None)
    if isinstance(success_message, str) and success_message:
        st.success(success_message)

    audited["reconciliation_status"] = audited["status"]
    if "audit_status" not in audited:
        audited["audit_status"] = ""
    audited["audit_status"] = audited["audit_status"].fillna("")
    audited["status"] = audited["audit_status"].where(
        audited["audit_status"].ne(""), audited["reconciliation_status"]
    )
    duplicate_mask = audited["duplicate_of_invoice_id"].notna()
    audited.loc[duplicate_mask, "status"] = "DUPLICATE_BILLING_BLOCKED"
    handled = audited["audit_status"].isin(HANDLED_STATUSES)
    exception_rows = audited[
        audited["reconciliation_status"].ne(TOUCHLESS_APPROVED)
        & (~handled | duplicate_mask)
    ]
    history_rows = audited[
        (audited["reconciliation_status"].eq(TOUCHLESS_APPROVED) | handled)
        & ~duplicate_mask
    ]

    metrics = summarize(audited)
    kpis = st.columns(4)
    kpis[0].metric("Invoices processed", f"{metrics['invoice_count']:,}")
    kpis[1].metric("Auto-approval rate", f"{metrics['stp_rate']:.1%}")
    kpis[2].metric("Discrepancy amount", f"+{format_money(metrics['prevented_overbill'])}")
    kpis[3].metric("Open exception backlog", f"{len(exception_rows):,}")

    carrier_summary, leakage_summary = summarize_carrier_billing(audited)
    with st.expander("Carrier Billing Performance & Analytics"):
        st.markdown("**Carrier billing accuracy**")
        carrier_display = carrier_summary.copy()
        carrier_display["Invoice Accuracy (Zero-Variance Matches)"] = (
            carrier_display.apply(
                lambda row: _format_invoice_accuracy(
                    row["Accuracy index"],
                    int(row["Clean invoices"]),
                    int(row["Invoices"]),
                ),
                axis=1,
            )
        )
        carrier_display["Recoverable Discrepancy ($)"] = carrier_display[
            "Overbill exposure"
        ].map(format_money)
        st.dataframe(
            carrier_display[
                [
                    "Carrier",
                    "Invoice Accuracy (Zero-Variance Matches)",
                    "Recoverable Discrepancy ($)",
                    "Primary leakage driver",
                ]
            ],
            use_container_width=True,
            hide_index=True,
        )
        st.markdown("**Financial leakage breakdown**")
        components.html(
            _leakage_chart_html(leakage_summary),
            height=max(90, len(leakage_summary) * 48 + 20),
            scrolling=False,
        )
        st.caption(
            "* Financial leakage excludes weight variances pending certified scale/cube dispute."
        )

    queue_view_label = f"Exception Queue ({len(exception_rows):,})"
    history_view_label = f"Audit History / Approved ({len(history_rows):,})"
    worklist_view = st.radio(
        "Worklist",
        [queue_view_label, history_view_label],
        horizontal=True,
        key="worklist_view",
    )
    showing_history = worklist_view == history_view_label
    if not showing_history:
        status_filter = st.radio(
            "Quick filter",
            ["All exceptions", "Disputed / flagged", "Under review"],
            horizontal=True,
            key="exception_filter",
        )
        visible = exception_rows.copy()
        if status_filter == "Disputed / flagged":
            visible = visible[
                visible["reconciliation_status"].isin(
                    [DISCREPANCY_FLAGGED, "DUPLICATE_BILLING_BLOCKED"]
                )
            ]
        elif status_filter == "Under review":
            visible = visible[
                visible["reconciliation_status"].isin(
                    [REVIEW_REQUIRED, REVIEW_INCOMPLETE]
                )
                | visible["audit_status"].eq("UNDER_REVIEW")
            ]
    else:
        visible = history_rows.copy()

    with st.container(horizontal=True):
        carriers = sorted(audited["carrier_name"].dropna().astype(str).unique())
        selected_carriers = st.multiselect(
            "Carrier", carriers, default=carriers, key="carrier_selection"
        )
        invoice_search = st.text_input("Invoice ID", placeholder="Filter invoices")

    visible = visible[visible["carrier_name"].astype("string").isin(selected_carriers)]
    if invoice_search:
        visible = visible[
            visible["invoice_number"].astype("string").str.contains(
                invoice_search, case=False, na=False
            )
        ]
    visible = visible.assign(
        _duplicate_priority=visible["duplicate_of_invoice_id"].notna()
    ).sort_values(
        ["_duplicate_priority", "total_overbill", "weight_variance_lbs"],
        ascending=[False, False, False],
        na_position="last",
    )

    st.subheader(
        "Exception Queue"
        if not showing_history
        else "Audit history and approved invoices"
    )
    if visible.empty:
        st.info("No invoices match this worklist and its filters.")
    else:
        display = pd.DataFrame(
            {
                "Invoice ID": visible["invoice_number"],
                "Carrier": visible["carrier_name"],
                "PO Number": visible["po_number"],
                "Status": visible["status"].map(_display_status),
                "Primary Root Cause": visible.apply(_primary_root_cause, axis=1),
                "Base": visible["base_variance"].map(_money_delta),
                "Fuel": visible["fuel_variance"].map(_money_delta),
                "Accessorials": visible["accessorial_variance"].map(_money_delta),
                "Weight": visible["weight_variance_lbs"].map(_weight_delta),
                "Total": visible["total_overbill"].map(format_money),
                "Receipt": visible["receipt_found"].map(
                    lambda found: "Found" if bool(found) else "Missing"
                ),
            }
        )
        components.html(
            _workbench_table_html(
                display,
                table_class="worklist-table",
                column_widths=[8, 10, 8, 13, 17, 8, 8, 11, 8, 10, 10],
                column_groups={
                    "Discrepancies": [
                        "Base", "Fuel", "Accessorials", "Weight", "Total"
                    ]
                },
            ),
            height=_table_component_height(len(display), header_rows=2),
            scrolling=False,
        )

        invoice_labels = {
            str(row["invoice_id"]): (
                f"{row['invoice_number']} ({row['invoice_id']})"
                if str(row["invoice_number"]) != str(row["invoice_id"])
                else str(row["invoice_number"])
            )
            for _, row in visible.iterrows()
        }
        selected_id = st.selectbox(
            "Select invoice",
            list(invoice_labels),
            format_func=invoice_labels.__getitem__,
            index=0,
            key=f"invoice_selector_{worklist_view}",
        )
        selected = visible.loc[visible["invoice_id"].astype(str).eq(selected_id)].iloc[0]
        selected_number = str(selected["invoice_number"])
        with st.container():
            if not selected["receipt_found"]:
                st.warning("No warehouse receipt was found for this purchase order.")
            st.markdown("#### 3-Way Reconciliation")
            comparison = _comparison_table(selected)
            components.html(
                _workbench_table_html(
                    comparison,
                    table_class="reconciliation-table",
                    column_widths=[29, 18, 18, 18, 17],
                ),
                height=_table_component_height(len(comparison)),
                scrolling=False,
            )
            if selected.get("root_cause") == UNAUTHORIZED_FUEL_CREEP:
                st.error("AIP Index Overcharge Detected")
            _render_fuel_audit_breakdown(selected, fuel_tolerance)
            financial_summary = st.columns(3)
            withheld = _numeric_value(selected.get("disputed_amount")) or 0.0
            financial_items = [
                (
                    "Total Invoiced Amount",
                    _format_aud_money(selected.get("total_invoiced")),
                    "#fafafa",
                ),
                (
                    "Deductions Withheld",
                    _format_withheld_amount(withheld),
                    "#e46b6b",
                ),
                (
                    "Net Approved Remittance",
                    _format_aud_money(selected.get("approved_remittance")),
                    "#fafafa",
                ),
            ]
            for column, (label, value, color) in zip(
                financial_summary, financial_items, strict=True
            ):
                column.markdown(
                    "<div>"
                    f"<div style=\"font-family:inherit;font-size:20px;line-height:normal;"
                    f"color:#fafafa;font-weight:400\">{escape(label)}</div>"
                    f"<div style=\"font-size:20px;font-weight:400;margin-top:10.5px;"
                    f"line-height:normal;color:{color}\">{escape(value)}</div></div>",
                    unsafe_allow_html=True,
                )
            rebuttal_sla = _rebuttal_sla(
                selected.get("rebuttal_deadline"), selected.get("audit_status")
            )
            if rebuttal_sla != "—":
                st.info(f"Rebuttal Window (SLA): {rebuttal_sla}")
            osd_notes = selected.get("osd_notes")
            if isinstance(osd_notes, str) and osd_notes.strip():
                st.warning(f"Warehouse OS&D note: {osd_notes}")

            if not showing_history:
                st.markdown(
                    '<div style="height:24px"></div>',
                    unsafe_allow_html=True,
                )
                st.markdown("#### Resolution")
                audit_note = st.text_area(
                    "",
                    key=f"audit_note_{selected_id}",
                    placeholder=(
                        "Record evidence, approval rationale, or the carrier action "
                        "required."
                    ),
                    label_visibility="collapsed",
                )
                action_columns = st.columns(3)
                actions = (
                    ("Mark Under Review", "UNDER_REVIEW"),
                    ("Approve with Override", "OVERRIDE_APPROVED"),
                    ("Request Revised Scale Ticket", "SCALE_TICKET_REQUESTED"),
                )
                for column, (label, audit_status) in zip(
                    action_columns, actions, strict=True
                ):
                    if column.button(
                        label,
                        key=f"action_{audit_status}_{selected_id}",
                        use_container_width=True,
                        disabled=(
                            audit_status == "OVERRIDE_APPROVED"
                            and pd.notna(selected.get("duplicate_of_invoice_id"))
                        ),
                    ):
                        _save_resolution(
                            client,
                            selected_id,
                            selected_number,
                            audit_status,
                            audit_note,
                        )

            if not showing_history or (
                selected["audit_status"]
                in {
                    "DISPUTED",
                    "REBUTTAL_RECEIVED",
                    "CLOSED_RESOLVED",
                    "CLOSED_OVERRIDDEN",
                }
            ):
                st.markdown("#### Carrier dispute memo")
                memo_key = f"dispute_memo_{selected_id}"
                generated_key = f"dispute_memo_generated_{selected_id}"
                unavailable_key = f"dispute_memo_unavailable_{selected_id}"
                if st.button(
                    _dispute_memo_button_label(
                        st.session_state.get(generated_key, False)
                    ),
                    key=f"generate_memo_{selected_id}",
                ):
                    st.session_state[generated_key] = True
                    st.session_state.pop(memo_key, None)
                    st.session_state.pop(f"dispute_email_body_{selected_id}", None)
                    st.session_state[unavailable_key] = False
                    with st.spinner("Drafting dispute memo..."):
                        try:
                            memo = draft_dispute(
                                selected.to_dict(),
                                api_key=st.secrets.get("OPENAI_API_KEY"),
                            )
                        except Exception:
                            st.session_state[unavailable_key] = True
                        else:
                            st.session_state[memo_key] = memo

                memo = st.session_state.get(memo_key)
                if st.session_state.get(unavailable_key, False):
                    st.error("AI is currently down. Please try again later.")
                elif memo:
                    st.caption(
                        "Generated draft. Review it for accuracy before sending."
                    )
                    st.code(memo, language="text")
                    dispatch_status = selected.get("email_dispatch_status")
                    if not isinstance(dispatch_status, str) or not dispatch_status:
                        dispatch_status = "NOT_QUEUED"
                    dispatch_seen_key = f"dispute_dispatch_seen_{selected_id}"
                    previous_dispatch_status = st.session_state.get(dispatch_seen_key)
                    if (
                        previous_dispatch_status == "PENDING"
                        and dispatch_status == "SENT"
                    ):
                        st.toast(
                            f"Outlook sent the dispute email to "
                            f"{selected.get('dispute_to_email', '')}."
                        )
                    st.session_state[dispatch_seen_key] = dispatch_status

                    packet_filename = _dispute_packet_filename(selected_number)
                    packet_pdf = generate_dispute_packet_pdf(
                        selected.to_dict(), memo
                    )
                    memo_actions = st.columns(3)
                    with memo_actions[0]:
                        if st.button(
                            "Send As Email",
                            key=f"open_dispute_email_{selected_id}",
                            disabled=dispatch_status in {"PENDING", "SENT"},
                        ):
                            st.session_state[
                                f"show_dispute_email_{selected_id}"
                            ] = True
                    with memo_actions[1]:
                        st.download_button(
                            "Download dispute memo",
                            data=memo,
                            file_name=f"{selected_number}_dispute.txt",
                            mime="text/plain",
                            key=f"download_memo_{selected_id}",
                        )
                    with memo_actions[2]:
                        st.download_button(
                            "Download Evidence Packet (PDF)",
                            data=packet_pdf,
                            file_name=packet_filename,
                            mime="application/pdf",
                            key=f"download_packet_{selected_id}",
                        )

                    composer_key = f"show_dispute_email_{selected_id}"
                    if st.session_state.get(composer_key, False):
                        with st.container(border=True):
                            st.markdown("#### Outlook dispute email")
                            st.caption(
                                "Power Automate must be authorized to send from this "
                                "Outlook address."
                            )
                            with st.form(f"dispute_email_form_{selected_id}"):
                                st.text_input(
                                    "From (Outlook account)",
                                    value=OUTLOOK_FROM_EMAIL,
                                    disabled=True,
                                    key=f"dispute_email_from_{selected_id}",
                                )
                                to_email = st.text_input(
                                    "To",
                                    value=_default_carrier_email(selected),
                                    key=f"dispute_email_to_{selected_id}",
                                    help=(
                                        "Enter one or more comma-separated carrier "
                                        "email addresses."
                                    ),
                                )
                                cc_email = st.text_input(
                                    "CC",
                                    value=(
                                        selected.get("dispute_cc_email")
                                        if isinstance(
                                            selected.get("dispute_cc_email"), str
                                        )
                                        else ""
                                    ),
                                    key=f"dispute_email_cc_{selected_id}",
                                )
                                subject = st.text_input(
                                    "Subject",
                                    value=(
                                        f"Billing Dispute: {selected_id} / "
                                        f"PO-{selected.get('po_number', '')}"
                                    ),
                                    key=f"dispute_email_subject_{selected_id}",
                                )
                                body = st.text_area(
                                    "Message",
                                    value=memo,
                                    height=260,
                                    key=f"dispute_email_body_{selected_id}",
                                )
                                attach_evidence = st.checkbox(
                                    f"Attach Verified Evidence Packet ({packet_filename})",
                                    value=True,
                                    key=f"attach_evidence_{selected_id}",
                                )
                                send_dispatch = st.form_submit_button(
                                    "Confirm & Send", type="primary"
                                )
                                discard_dispatch = st.form_submit_button("Discard")

                        if send_dispatch:
                            try:
                                invoice_id = selected.get("invoice_id")
                                if not isinstance(invoice_id, str) or not invoice_id:
                                    raise ValueError(
                                        "The selected invoice has no Supabase invoice_id."
                                    )
                                carrier_name = selected.get("carrier_name")
                                if not isinstance(carrier_name, str) or not carrier_name:
                                    raise ValueError(
                                        "The selected invoice has no carrier name."
                                    )
                                queue_dispute_email(
                                    client,
                                    invoice_id,
                                    carrier_name,
                                    to_email=to_email,
                                    cc_email=cc_email,
                                    from_email=OUTLOOK_FROM_EMAIL,
                                    subject=subject,
                                    body=body or "",
                                    evidence_packet=(
                                        generate_dispute_packet_pdf(
                                            selected.to_dict(), body or ""
                                        )
                                        if attach_evidence
                                        else None
                                    ),
                                    evidence_filename=(
                                        packet_filename if attach_evidence else None
                                    ),
                                )
                            except Exception as error:
                                st.error(
                                    f"Could not queue the dispute email for "
                                    f"{selected_id}: {error}"
                                )
                            else:
                                st.session_state[composer_key] = False
                                st.session_state["dispatch_success_message"] = (
                                    "Dispute logged! Outbound dispatch queued for "
                                    "Power Automate Desktop to process via Outlook."
                                )
                                st.rerun()
                        elif discard_dispatch:
                            st.session_state[composer_key] = False

                    if dispatch_status == "PENDING":
                        st.info("Dispute email is pending Outlook dispatch.")
                    elif dispatch_status == "SENT":
                        st.success("Dispute email sent through Outlook.")
                    elif dispatch_status == "FAILED":
                        dispatch_error = selected.get("error_message")
                        error_detail = (
                            f" Details: {dispatch_error}"
                            if isinstance(dispatch_error, str) and dispatch_error
                            else ""
                        )
                        st.error(
                            "Power Automate Desktop could not send this dispute email."
                            f"{error_detail}"
                        )
                else:
                    st.caption("Generate a draft when you're ready to review it.")

            if selected["audit_status"] in {"DISPUTED", "REBUTTAL_RECEIVED"}:
                st.markdown("#### Dispute lifecycle")
                lifecycle_note = st.text_area(
                    "Carrier response / resolution note",
                    key=f"lifecycle_note_{selected_id}",
                    placeholder="Summarize the carrier's response or credit memo.",
                )
                if selected["audit_status"] == "DISPUTED":
                    lifecycle_actions = (
                        ("Record Carrier Rebuttal", "REBUTTAL_RECEIVED"),
                        ("Close — Credit Memo Issued", "CLOSED_RESOLVED"),
                        ("Close — Dispute Conceded", "CLOSED_OVERRIDDEN"),
                    )
                else:
                    lifecycle_actions = (
                        ("Close — Credit Memo Issued", "CLOSED_RESOLVED"),
                        ("Close — Dispute Conceded", "CLOSED_OVERRIDDEN"),
                    )
                for column, (label, audit_status) in zip(
                    st.columns(len(lifecycle_actions)),
                    lifecycle_actions,
                    strict=True,
                ):
                    if column.button(
                        label,
                        key=f"lifecycle_{audit_status}_{selected_id}",
                        use_container_width=True,
                    ):
                        _save_resolution(
                            client,
                            selected_id,
                            selected_number,
                            audit_status,
                            lifecycle_note,
                        )

            try:
                events = fetch_invoice_audit_events(client, selected_id)
            except Exception as error:
                st.error(
                    f"Could not load the audit trail for {selected_id}. Apply the "
                    "invoice audit workbench migration and check Supabase access. "
                    f"Details: {error}"
                )
            else:
                st.markdown("#### Audit trail")
                if events:
                    trail = pd.DataFrame(events)
                    st.dataframe(
                        trail[
                            [
                                column
                                for column in (
                                    "created_at",
                                    "previous_status",
                                    "audit_status",
                                    "audit_note",
                                )
                                if column in trail
                            ]
                        ],
                        use_container_width=True,
                        hide_index=True,
                    )
                else:
                    st.caption("No resolution events have been recorded.")

render_audit()

"""Streamlit workbench for exception-based three-way freight invoice audits."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from numbers import Real

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from src.agent import draft_dispute
from src.dispute_packet import generate_dispute_packet_pdf
from src.reconciliation_engine import (
    DISCREPANCY_FLAGGED,
    REVIEW_INCOMPLETE,
    REVIEW_REQUIRED,
    TOUCHLESS_APPROVED,
    summarize_carrier_billing,
    summarize,
    write_discrepancy_queue,
)
from src.supabase_data import (
    create_supabase_client,
    fetch_dispute_dispatches,
    fetch_invoice_audit_events,
    queue_dispute_email,
    reconcile_supabase_data,
    update_invoice_audit,
)

PROJECT_ROOT = Path(__file__).resolve().parent
OUTLOOK_FROM_EMAIL = "nadiaravindra07@outlook.com"
HANDLED_STATUSES = {
    "OVERRIDE_APPROVED",
    "DISPUTED",
    "SCALE_TICKET_REQUESTED",
    "REBUTTAL_RECEIVED",
    "CLOSED_RESOLVED",
    "CLOSED_OVERRIDDEN",
}
AUDIT_STATUS_LABELS = {
    "OVERRIDE_APPROVED": "Override approved",
    "DISPUTED": "Disputed",
    "SCALE_TICKET_REQUESTED": "Scale ticket requested",
    "UNDER_REVIEW": "Under review",
    "REBUTTAL_RECEIVED": "REBUTTAL_RECEIVED (Carrier rebuttal received)",
    "CLOSED_RESOLVED": "CLOSED_RESOLVED (Credit Memo Issued)",
    "CLOSED_OVERRIDDEN": "CLOSED_OVERRIDDEN (Dispute Conceded)",
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


def _format_invoice_accuracy(
    accuracy: float, clean_invoices: int, invoice_count: int
) -> str:
    if accuracy >= 1:
        indicator = "🟢"
    elif accuracy >= 0.5:
        indicator = "🟡"
    else:
        indicator = "🔴"
    return (
        f"{indicator} {accuracy:.0%} "
        f"({clean_invoices} / {invoice_count} Clean)"
    )


def _dispute_memo_button_label(has_generated: bool) -> str:
    return "Regenerate dispute memo" if has_generated else "Generate dispute memo"


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
    return f"{number:,.0f} lb"


def _money_delta(value: object) -> str:
    number = _numeric_value(value)
    if number is None:
        return "N/A"
    if number == 0:
        return "$0.00"
    return f"{'+' if number > 0 else '-'}${abs(number):,.2f}"


def _weight_delta(value: object) -> str:
    number = _numeric_value(value)
    if number is None:
        return "N/A"
    if number == 0:
        return "0 lb"
    return f"{number:+,.0f} lb"


def _primary_root_cause(row: pd.Series) -> str:
    duplicate_of = row.get("duplicate_of_invoice_id")
    if isinstance(duplicate_of, str) and duplicate_of.strip():
        return f"Duplicate bill — previously processed {duplicate_of}"
    if not bool(row.get("receipt_found")):
        return "⚪ Missing Scale Receipt"

    base_variance = _numeric_value(row.get("base_variance")) or 0.0
    fuel_variance = _numeric_value(row.get("fuel_variance")) or 0.0
    unauthorized_items = row.get("unauthorized_accessorial_items", [])
    accessorial_codes = sorted(
        {
            str(item.get("code") or "UNKNOWN").strip().upper()
            for item in unauthorized_items
            if isinstance(item, dict)
        }
    ) if isinstance(unauthorized_items, list) else []

    if accessorial_codes:
        if len(accessorial_codes) > 1:
            return f"🔴 Unapproved {' & '.join(accessorial_codes)}"
        code = accessorial_codes[0]
        matching_item = next(
            (
                item
                for item in unauthorized_items
                if isinstance(item, dict)
                and str(item.get("code") or "UNKNOWN").strip().upper() == code
            ),
            {},
        )
        description = str(matching_item.get("description") or "").strip()
        if description.startswith("Driver "):
            description = description.removeprefix("Driver ")
        fee_cause = f"{description} ({code})" if description else code
        if base_variance > 0:
            return f"🔴 Base + {fee_cause}"
        return f"🔴 Unapproved {code}"

    if base_variance > 0 and fuel_variance > 0:
        return "🟡 Base + Fuel Variance"
    if base_variance > 0:
        return "🔴 Base Rate Variance"
    if fuel_variance > 0:
        return "🟡 Fuel Index Pegged"
    if (_numeric_value(row.get("weight_variance_lbs")) or 0.0) > 0:
        return "🔴 Weight Overweight"
    return "🟡 Review Required"


def _workbench_table_html(
    frame: pd.DataFrame,
    *,
    table_class: str,
    column_widths: list[int],
) -> str:
    header = "".join(
        f'<th scope="col" style="width:{width}%">{escape(str(column))}</th>'
        for column, width in zip(frame.columns, column_widths, strict=True)
    )
    rows = []
    for _, row in frame.iterrows():
        cells = []
        status = str(row.get("Status", ""))
        for column, value in row.items():
            text = str(value)
            classes = []
            if column == "Status":
                classes.append("status-cell")
                if status == DISCREPANCY_FLAGGED:
                    classes.append("status-flagged")
                elif status == "DUPLICATE_BILLING_BLOCKED":
                    classes.append("status-duplicate")
                elif status == REVIEW_REQUIRED:
                    classes.append("status-review")
                elif status == REVIEW_INCOMPLETE:
                    classes.append("status-incomplete")
                elif status == TOUCHLESS_APPROVED:
                    classes.append("status-approved")
            elif column == "Receipt":
                classes.append("receipt-found" if text == "Found" else "receipt-missing")
            elif column == "Discrepancy (Total Δ)" and status == DISCREPANCY_FLAGGED:
                classes.append("discrepancy-flagged")
            elif column == "Duplicate Match" and text != "—":
                classes.extend(["discrepancy-flagged", "duplicate-match"])
            elif column == "Variance" and (
                text.startswith("+") or "Overweight" in text
            ):
                classes.append("variance-flagged")
            class_attribute = f' class="{" ".join(classes)}"' if classes else ""
            escaped_text = escape(text)
            cells.append(
                f"<td{class_attribute} title=\"{escaped_text}\">{escaped_text}</td>"
            )
        rows.append(f"<tr>{''.join(cells)}</tr>")
    return (
        f'<div class="workbench-table-wrap"><table class="workbench-table {table_class}">'
        f"<thead><tr>{header}</tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
    )


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
    result = [
        {
            "Component": "Base freight",
            "Invoiced (Carrier)": format_money(row.get("billed_base_freight")),
            "Authorized (PO)": format_money(
                row.get("contracted_base_rate", row.get("contracted_base_freight"))
            ),
            "Physical (WMS)": "—",
            "Variance": _money_delta(row.get("base_variance")),
        },
        {
            "Component": "Fuel surcharge",
            "Invoiced (Carrier)": format_money(row.get("billed_fuel_surcharge")),
            "Authorized (PO)": format_money(
                row.get("contracted_fuel_surcharge", row.get("agreed_fuel_surcharge"))
            ),
            "Physical (WMS)": "—",
            "Variance": _money_delta(row.get("fuel_variance")),
        },
    ]

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
                    "Component": f"Accessorial — {code or 'UNKNOWN'}: {item['description']}",
                    "Invoiced (Carrier)": format_money(billed_amount),
                    "Authorized (PO)": format_money(authorized_amount),
                    "Physical (WMS)": "—",
                    "Variance": _money_delta(billed_amount - authorized_amount),
                }
            )
    else:
        result.append(
            {
                "Component": "Accessorial fees (legacy aggregate)",
                "Invoiced (Carrier)": format_money(row.get("billed_accessorial_fee")),
                "Authorized (PO)": format_money(row.get("max_accessorial_allowance")),
                "Physical (WMS)": "—",
                "Variance": _money_delta(row.get("accessorial_variance")),
            }
        )

    weight_difference = billed_weight - scale_weight if (
        billed_weight is not None and scale_weight is not None
    ) else None
    weight_variance_label = _weight_delta(weight_difference)
    result.extend(
        [
            {
                "Component": "├─ Gross Vehicle Wt",
                "Invoiced (Carrier)": "—",
                "Authorized (PO)": "—",
                "Physical (WMS)": format_weight(row.get("gross_weight_lbs")),
                "Variance": "—",
            },
            {
                "Component": "└─ Empty truck + trailer tare weight",
                "Invoiced (Carrier)": "—",
                "Authorized (PO)": "—",
                "Physical (WMS)": format_weight(row.get("tare_weight_lbs")),
                "Variance": "—",
            },
            {
                "Component": "Chargeable / certified net weight",
                "Invoiced (Carrier)": format_weight(billed_weight),
                "Authorized (PO)": format_weight(expected_weight),
                "Physical (WMS)": format_weight(scale_weight),
                "Variance": weight_variance_label,
            },
        ]
    )
    return pd.DataFrame(result)


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
    .workbench-table-wrap {
        width: 100%;
        overflow-x: auto;
        margin-bottom: 0.75rem;
    }
    table.worklist-table {
        min-width: 1180px;
    }
    table.workbench-table {
        width: 100%;
        table-layout: fixed;
        border-collapse: collapse;
        font-size: 16px;
        line-height: 1.4;
    }
    table.workbench-table th,
    table.workbench-table td {
        border: 1px solid #34383f;
        padding: 3px 2px;
        text-align: left;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    table.workbench-table th {
        background: #15191f;
        color: #f1f3f5;
        font-weight: 600;
        white-space: normal;
    }
    table.workbench-table td {
        background: #0e1117;
    }
    table.workbench-table .status-cell,
    table.workbench-table .receipt-found,
    table.workbench-table .receipt-missing {
        color: #fff;
        text-align: center;
        font-size: 14px;
        font-weight: 600;
    }
    table.workbench-table .status-flagged,
    table.workbench-table .status-duplicate,
    table.workbench-table .receipt-missing {
        background: #c63b40;
    }
    table.workbench-table .status-duplicate,
    table.workbench-table .duplicate-match {
        white-space: normal;
        overflow-wrap: anywhere;
    }
    table.workbench-table .status-review {
        background: #b58800;
    }
    table.workbench-table .status-incomplete {
        background: #c66a16;
    }
    table.workbench-table .status-approved,
    table.workbench-table .receipt-found {
        background: #18834b;
    }
    table.workbench-table .discrepancy-flagged {
        background: #dc454b;
        color: #fff;
        font-weight: 700;
        text-align: right;
    }
    table.workbench-table .variance-flagged {
        color: #e46b6b;
        font-weight: 600;
        text-align: right;
    }
    table.reconciliation-table td:not(:first-child) {
        text-align: right;
    }
    table.reconciliation-table td:first-child {
        white-space: normal;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

with st.sidebar:
    st.subheader("Audit controls")
    fuel_tolerance = st.slider(
        "Fuel tolerance ($)",
        min_value=0.0,
        max_value=25.0,
        value=1.0,
        step=0.5,
        help="Fuel variance within this dollar amount does not trigger an exception.",
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
        selected_carriers = st.multiselect("Carrier", carriers, default=carriers)
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
                "Status": visible["status"].map(
                    lambda status: AUDIT_STATUS_LABELS.get(status, status)
                ),
                "Primary Root Cause": visible.apply(_primary_root_cause, axis=1),
                "Duplicate Match": visible["duplicate_of_invoice_id"].map(
                    lambda original: (
                        f"Duplicate bill of previously processed {original}"
                        if isinstance(original, str) and original.strip()
                        else "—"
                    )
                ),
                "Base Δ": visible["base_variance"].map(_money_delta),
                "Fuel Δ": visible["fuel_variance"].map(_money_delta),
                "Acc Δ": visible["accessorial_variance"].map(_money_delta),
                "Wt Δ": visible["weight_variance_lbs"].map(_weight_delta),
                "Discrepancy (Total Δ)": visible["total_overbill"].map(format_money),
                "Rebuttal Window (SLA)": visible.apply(
                    lambda row: _rebuttal_sla(
                        row.get("rebuttal_deadline"), row.get("audit_status")
                    ),
                    axis=1,
                ),
                "Receipt": visible["receipt_found"].map(
                    lambda found: "Found" if bool(found) else "Missing"
                ),
            }
        )
        st.markdown(
            _workbench_table_html(
                display,
                table_class="worklist-table",
                column_widths=[6, 8, 6, 11, 14, 14, 5, 5, 5, 5, 8, 8, 5],
            ),
            unsafe_allow_html=True,
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
            st.markdown(
                _workbench_table_html(
                    comparison,
                    table_class="reconciliation-table",
                    column_widths=[29, 18, 18, 18, 17],
                ),
                unsafe_allow_html=True,
            )
            financial_summary = st.columns(3)
            financial_summary[0].metric(
                "Total Invoiced Amount", format_money(selected.get("total_invoiced"))
            )
            financial_summary[1].metric(
                "Withheld / Disputed Amount",
                f"-{format_money(selected.get('disputed_amount'))}",
            )
            financial_summary[2].metric(
                "Net Approved Remittance",
                format_money(selected.get("approved_remittance")),
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

            if selected["reconciliation_status"] == DISCREPANCY_FLAGGED or (
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
                                dispatch_id = queue_dispute_email(
                                    client,
                                    invoice_id,
                                    carrier_name,
                                    to_email=to_email,
                                    cc_email=cc_email,
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
                                    "Dispute logged in dispatch queue "
                                    f"(ID: {dispatch_id})! Power Automate is routing "
                                    "the email through Outlook."
                                )
                                st.rerun()
                        elif discard_dispatch:
                            st.session_state[composer_key] = False

                    if dispatch_status == "PENDING":
                        st.info("Dispute email is pending Outlook dispatch.")
                    elif dispatch_status == "SENT":
                        st.success("Dispute email sent through Outlook.")
                    elif dispatch_status == "FAILED":
                        st.error(
                            "Outlook could not send this dispute email. Correct the "
                            "details and queue it again."
                        )
                else:
                    st.caption("Generate a draft when you're ready to review it.")

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

    if st.button("Export discrepancy queue for Power Automate"):
        try:
            export_rows = exception_rows.copy()
            export_rows["status"] = export_rows["reconciliation_status"]
            queue = write_discrepancy_queue(
                export_rows,
                PROJECT_ROOT / "automation" / "discrepancy_queue.json",
            )
        except Exception as error:
            st.error(f"Could not export the discrepancy queue: {error}")
        else:
            st.success(f"Exported {len(queue)} open exception(s).")


render_audit()

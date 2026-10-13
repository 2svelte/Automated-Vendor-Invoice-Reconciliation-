"""Streamlit workbench for exception-based three-way freight invoice audits."""

from __future__ import annotations

import math
from html import escape
from pathlib import Path
from numbers import Real

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from src.agent import draft_dispute
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
    fetch_invoice_audit_events,
    reconcile_supabase_data,
    update_invoice_audit,
)

PROJECT_ROOT = Path(__file__).resolve().parent
HANDLED_STATUSES = {"OVERRIDE_APPROVED", "DISPUTED", "SCALE_TICKET_REQUESTED"}
AUDIT_STATUS_LABELS = {
    "OVERRIDE_APPROVED": "Override approved",
    "DISPUTED": "Disputed",
    "SCALE_TICKET_REQUESTED": "Scale ticket requested",
    "UNDER_REVIEW": "Under review",
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
    invoice_number: str,
    audit_status: str,
    audit_note: str,
) -> None:
    try:
        update_invoice_audit(client, invoice_number, audit_status, audit_note)
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
    table.workbench-table .receipt-missing {
        background: #c63b40;
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
    if st.button("Refresh Supabase data", type="primary", use_container_width=True):
        st.rerun()


@st.fragment(run_every="15s")
def render_audit() -> None:
    try:
        client = get_supabase_client()
        audited = reconcile_supabase_data(
            client,
            fuel_tolerance=fuel_tolerance,
            weight_tolerance_pct=weight_tolerance_pct,
        )
    except Exception as error:
        st.error(f"Could not load and audit Supabase data: {error}")
        return

    audited["reconciliation_status"] = audited["status"]
    if "audit_status" not in audited:
        audited["audit_status"] = ""
    audited["audit_status"] = audited["audit_status"].fillna("")
    audited["status"] = audited["audit_status"].where(
        audited["audit_status"].ne(""), audited["reconciliation_status"]
    )
    handled = audited["audit_status"].isin(HANDLED_STATUSES)
    exception_rows = audited[
        audited["reconciliation_status"].ne(TOUCHLESS_APPROVED) & ~handled
    ]
    history_rows = audited[
        audited["reconciliation_status"].eq(TOUCHLESS_APPROVED) | handled
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
                visible["reconciliation_status"].eq(DISCREPANCY_FLAGGED)
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
    visible = visible.sort_values(
        ["total_overbill", "weight_variance_lbs"],
        ascending=[False, False],
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
                "Status": visible["status"],
                "Primary Root Cause": visible.apply(_primary_root_cause, axis=1),
                "Base Δ": visible["base_variance"].map(_money_delta),
                "Fuel Δ": visible["fuel_variance"].map(_money_delta),
                "Acc Δ": visible["accessorial_variance"].map(_money_delta),
                "Wt Δ": visible["weight_variance_lbs"].map(_weight_delta),
                "Discrepancy (Total Δ)": visible["total_overbill"].map(format_money),
                "Receipt": visible["receipt_found"].map(
                    lambda found: "Found" if bool(found) else "Missing"
                ),
            }
        )
        st.markdown(
            _workbench_table_html(
                display,
                table_class="worklist-table",
                column_widths=[8, 11, 8, 14, 19, 6, 6, 6, 6, 10, 6],
            ),
            unsafe_allow_html=True,
        )

        selected_id = st.selectbox(
            "Select invoice",
            visible["invoice_number"].tolist(),
            index=0,
            key=f"invoice_selector_{worklist_view}",
        )
        selected = visible.loc[visible["invoice_number"].eq(selected_id)].iloc[0]
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
                action_columns = st.columns(4)
                actions = (
                    ("Mark Under Review", "UNDER_REVIEW"),
                    ("Dispute Carrier Bill", "DISPUTED"),
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
                    ):
                        _save_resolution(
                            client,
                            selected_id,
                            audit_status,
                            audit_note,
                        )

            if selected["reconciliation_status"] == DISCREPANCY_FLAGGED or (
                selected["audit_status"] == "DISPUTED"
            ):
                st.markdown("#### Carrier dispute memo")
                memo_key = f"dispute_memo_{selected_id}"
                generated_key = f"dispute_memo_generated_{selected_id}"
                if st.button(
                    _dispute_memo_button_label(
                        st.session_state.get(generated_key, False)
                    ),
                    key=f"generate_memo_{selected_id}",
                ):
                    st.session_state[generated_key] = True
                    with st.spinner("Drafting dispute memo..."):
                        memo = draft_dispute(
                            selected.to_dict(),
                            api_key=st.secrets.get("OPENAI_API_KEY"),
                        )
                    st.session_state[memo_key] = memo

                memo = st.session_state.get(memo_key)
                if memo:
                    if "DETERMINISTIC FALLBACK DRAFT" in memo:
                        st.warning(
                            "Live drafting is unavailable or no OpenAI API key is "
                            "configured. This is a deterministic fallback draft."
                        )
                    else:
                        st.caption(
                            "Generated draft. Review it for accuracy before sending."
                        )
                    st.code(memo, language="text")
                    st.download_button(
                        "Download dispute memo",
                        data=memo,
                        file_name=f"{selected_id}_dispute.txt",
                        mime="text/plain",
                        key=f"download_memo_{selected_id}",
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

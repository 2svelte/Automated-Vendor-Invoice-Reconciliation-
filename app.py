"""Streamlit workbench for exception-based three-way freight invoice audits."""

from __future__ import annotations

import math
from pathlib import Path
from numbers import Real

import pandas as pd
import streamlit as st

from src.reconciliation_engine import (
    DISCREPANCY_FLAGGED,
    REVIEW_INCOMPLETE,
    REVIEW_REQUIRED,
    TOUCHLESS_APPROVED,
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


def _is_missing(value: object) -> bool:
    return _numeric_value(value) is None


def format_money(value: object) -> str:
    number = _numeric_value(value)
    if number is None:
        return "N/A"
    return f"${number:,.2f}"


def format_weight(value: object) -> str:
    number = _numeric_value(value)
    if number is None:
        return "N/A"
    return f"{number:,.0f} lb"


def _exception_reasons(row: pd.Series) -> str:
    reasons = []
    for column, label in (
        ("base_variance", "Base rate"),
        ("fuel_variance", "Fuel surcharge"),
        ("accessorial_variance", "Accessorial"),
    ):
        value = _numeric_value(row.get(column))
        if value is not None and value > 0:
            reasons.append(f"{label} {format_money(value)} over")

    weight_variance = _numeric_value(row.get("weight_variance_lbs"))
    if weight_variance is not None and weight_variance > 0:
        reasons.append(f"Weight +{format_weight(weight_variance)} beyond buffer")

    if row.get("reconciliation_status") == REVIEW_INCOMPLETE:
        reasons.append("Missing receipt")
    return " · ".join(reasons) if reasons else "Review required"


def highlight_worklist(frame: pd.DataFrame):
    def row_style(row: pd.Series) -> list[str]:
        group = row.get("Queue")
        if group == "Disputed / flagged":
            color = "background-color: #fde2e0; color: #781d18"
        elif group == "Under review":
            color = "background-color: #fff4cc; color: #654b00"
        elif group == "Auto-approved":
            color = "background-color: #e2f4e8; color: #195b31"
        else:
            color = ""
        return [color] * len(row)

    return frame.style.apply(row_style, axis=1)


def _dispute_memo(row: pd.Series) -> str:
    invoice_number = str(row["invoice_number"])
    po_number = str(row["po_number"])
    carrier = str(row["carrier_name"])
    overbill = _numeric_value(row.get("total_overbill")) or 0.0
    billed_total = sum(
        _numeric_value(row.get(column)) or 0.0
        for column in (
            "billed_base_freight",
            "billed_fuel_surcharge",
            "billed_accessorial_fee",
        )
    )
    remittance = max(0.0, billed_total - overbill)
    reasons = []
    for column, label in (
        ("base_variance", "Base rate"),
        ("fuel_variance", "Fuel surcharge"),
        ("accessorial_variance", "Unapproved accessorial"),
    ):
        value = _numeric_value(row.get(column))
        if value is not None and value > 0:
            note = (
                " (fee type is not identified in the source invoice)"
                if column == "accessorial_variance"
                else ""
            )
            reasons.append(f"{label} exceeded authorization by {format_money(value)}{note}")

    explanation = "; ".join(reasons) or "Please review the attached rate and receipt details"
    return (
        f"To: {carrier} Disputes Team\n"
        f"Subject: Rate Discrepancy Notice - Invoice {invoice_number} / PO {po_number}\n\n"
        f"Dear {carrier} Disputes Team,\n\n"
        f"Invoice {invoice_number} has a disputed overbill of {format_money(overbill)}. "
        f"{explanation}. Please issue a revised credit memo. "
        f"Proposed remittance is {format_money(remittance)}.\n\n"
        "Regards,\nAccounts Payable"
    )


def _comparison_table(row: pd.Series) -> pd.DataFrame:
    billed_weight = _numeric_value(row.get("billed_weight_lbs"))
    scale_weight = _numeric_value(row.get("actual_scale_weight_lbs"))
    components = [
        (
            "Base freight",
            row.get("billed_base_freight"),
            row.get("contracted_base_freight"),
            None,
            row.get("base_variance"),
            "money",
        ),
        (
            "Fuel surcharge",
            row.get("billed_fuel_surcharge"),
            row.get("agreed_fuel_surcharge"),
            None,
            row.get("fuel_variance"),
            "money",
        ),
        (
            "Accessorial fees (type unavailable)",
            row.get("billed_accessorial_fee"),
            row.get("max_accessorial_allowance"),
            None,
            row.get("accessorial_variance"),
            "money",
        ),
        (
            "Gross weight",
            row.get("billed_weight_lbs"),
            None,
            row.get("actual_scale_weight_lbs"),
            billed_weight - scale_weight
            if billed_weight is not None and scale_weight is not None
            else None,
            "weight",
        ),
    ]
    result = []
    for label, billed, authorized, physical, delta, kind in components:
        if kind == "money":
            result.append(
                {
                    "Component": label,
                    "Invoiced (Carrier)": format_money(billed),
                    "Authorized (PO)": format_money(authorized),
                    "Physical (WMS)": "—",
                    "Variance": format_money(delta),
                }
            )
        else:
            result.append(
                {
                    "Component": label,
                    "Invoiced (Carrier)": format_weight(billed),
                    "Authorized (PO)": "—",
                    "Physical (WMS)": format_weight(physical),
                    "Variance": (
                        "N/A"
                        if _is_missing(delta)
                        else f"{delta:+,.0f} lb"
                    ),
                }
            )
    return pd.DataFrame(result)


def _save_resolution(
    client,
    invoice_number: str,
    audit_status: str,
    auditor_name: str,
    audit_note: str,
) -> None:
    try:
        update_invoice_audit(
            client, invoice_number, audit_status, auditor_name, audit_note
        )
    except Exception as error:
        st.error(f"Could not save the resolution for {invoice_number}: {error}")
        return
    st.success(f"{invoice_number} updated: {AUDIT_STATUS_LABELS[audit_status]}.")
    st.rerun()


st.set_page_config(page_title="Freight Audit Workbench", page_icon="FA", layout="wide")
st.title("Freight Reconciliation & Exception Management")
st.caption(
    "Review only invoices that need a decision. Reconciliation is grounded in the "
    "carrier invoice, ERP purchase order, and warehouse receipt."
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
        "Exception queue"
        if not showing_history
        else "Audit history and approved invoices"
    )
    if visible.empty:
        st.info("No invoices match this worklist and its filters.")
    else:
        display = pd.DataFrame(
            {
                "Invoice": visible["invoice_number"],
                "Carrier": visible["carrier_name"],
                "PO": visible["po_number"],
                "Status": visible.apply(
                    lambda row: AUDIT_STATUS_LABELS.get(
                        row["audit_status"], row["status"]
                    ),
                    axis=1,
                ),
                "Variance detail": visible.apply(_exception_reasons, axis=1),
                "Discrepancy": visible["total_overbill"].map(format_money),
                "Weight delta": visible["weight_variance_lbs"].map(format_weight),
                "Receipt": visible["receipt_found"].map(
                    lambda found: "Found" if bool(found) else "Missing"
                ),
            }
        )
        if not showing_history:
            display["Queue"] = visible.apply(
                lambda row: (
                    "Disputed / flagged"
                    if row["reconciliation_status"] == DISCREPANCY_FLAGGED
                    else "Under review"
                ),
                axis=1,
            )
        else:
            display["Queue"] = visible.apply(
                lambda row: (
                    "Disputed / flagged"
                    if row["audit_status"] == "DISPUTED"
                    else (
                        "Auto-approved"
                        if row["audit_status"] == "OVERRIDE_APPROVED"
                        or row["reconciliation_status"] == TOUCHLESS_APPROVED
                        else "Under review"
                    )
                ),
                axis=1,
            )
        st.dataframe(
            highlight_worklist(display),
            use_container_width=True,
            hide_index=True,
            column_config={"Queue": None},
        )

        selected_id = st.selectbox(
            "Open invoice",
            visible["invoice_number"].tolist(),
            index=0,
            key=f"invoice_selector_{worklist_view}",
        )
        selected = visible.loc[visible["invoice_number"].eq(selected_id)].iloc[0]
        with st.expander(f"{selected_id} — 3-way audit and resolution", expanded=True):
            st.caption(
                f"{selected['carrier_name']} · PO {selected['po_number']} · "
                f"{selected['origin']} → {selected['destination']}"
            )
            st.dataframe(
                _comparison_table(selected),
                use_container_width=True,
                hide_index=True,
            )
            accessorial_variance = _numeric_value(
                selected.get("accessorial_variance")
            )
            if accessorial_variance is not None and accessorial_variance > 0:
                st.warning(
                    "The source invoice contains one aggregate accessorial amount; "
                    "its fee type (for example, liftgate or detention) is not recorded."
                )
            if not selected["receipt_found"]:
                st.warning("No warehouse receipt was found for this purchase order.")
            else:
                weight_variance = _numeric_value(
                    selected.get("weight_variance_lbs")
                )
                if weight_variance is not None and weight_variance > 0:
                    st.warning(
                        f"Carrier weight exceeds the {weight_tolerance_pct:g}% buffer by "
                        f"{format_weight(weight_variance)}."
                    )

            st.write(
                f"Current audit status: **"
                f"{AUDIT_STATUS_LABELS.get(selected['audit_status'], selected['status'])}**"
            )

            if not showing_history:
                st.markdown("#### Resolution")
                auditor_name = st.text_input(
                    "Auditor name", key="auditor_name"
                )
                audit_note = st.text_area(
                    "Resolution notes / rationale",
                    key=f"audit_note_{selected_id}",
                    placeholder=(
                        "Record evidence, approval rationale, or the carrier action "
                        "required."
                    ),
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
                        if not auditor_name.strip() or not audit_note.strip():
                            st.error("Enter an auditor name and resolution note first.")
                        else:
                            _save_resolution(
                                client,
                                selected_id,
                                audit_status,
                                auditor_name,
                                audit_note,
                            )

            if selected["reconciliation_status"] == DISCREPANCY_FLAGGED or (
                selected["audit_status"] == "DISPUTED"
            ):
                memo = _dispute_memo(selected)
                st.markdown("#### Carrier dispute memo")
                st.code(memo, language="text")
                st.download_button(
                    "Download dispute memo",
                    data=memo,
                    file_name=f"{selected_id}_dispute.txt",
                    mime="text/plain",
                    key=f"download_memo_{selected_id}",
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
                                    "auditor_name",
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

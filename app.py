"""Streamlit workbench for exception-based three-way freight invoice audits."""

from __future__ import annotations

import math
from pathlib import Path
from numbers import Real

import pandas as pd
import streamlit as st

from src.agent import draft_dispute
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


def _exception_reasons(row: pd.Series, weight_tolerance_pct: float = 2.0) -> str:
    if row.get("reconciliation_status") == TOUCHLESS_APPROVED:
        return "No discrepancies — within approval thresholds."

    reasons = []
    unauthorized_items = row.get("unauthorized_accessorial_items", [])
    if isinstance(unauthorized_items, list) and unauthorized_items:
        reasons.extend(
            (
                f"Unauthorized accessorial {item.get('code') or 'UNKNOWN'} "
                f"({item.get('description') or 'description unavailable'}): "
                f"{format_money(item.get('overage'))}."
            )
            for item in unauthorized_items
        )
    for column, label, include_currency in (
        ("base_variance", "Base rate", False),
        ("fuel_variance", "Fuel surcharge", False),
        ("accessorial_variance", "Accessorial", True),
    ):
        value = _numeric_value(row.get(column))
        if value is not None and value > 0 and not (
            column == "accessorial_variance" and unauthorized_items
        ):
            amount = format_money(value) if include_currency else f"{value:,.2f}"
            reasons.append(f"{label}: {amount} over.")

    weight_variance = _numeric_value(row.get("weight_variance_lbs"))
    if weight_variance is not None and weight_variance > 0:
        reasons.append(
            f"Carrier weight exceeds the {weight_tolerance_pct:g}% buffer by "
            f"{format_weight(weight_variance)}."
        )

    if row.get("reconciliation_status") == REVIEW_INCOMPLETE:
        reasons.append("Missing receipt.")
    return " ".join(reasons) if reasons else "Review required."


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


def _comparison_table(row: pd.Series) -> pd.DataFrame:
    billed_weight = _numeric_value(
        row.get("billed_chargeable_weight", row.get("billed_weight_lbs"))
    )
    scale_weight = _numeric_value(
        row.get("certified_net_scale_weight", row.get("actual_scale_weight_lbs"))
    )
    expected_weight = row.get(
        "contracted_expected_weight", row.get("expected_weight")
    )
    components = [
        (
            "Base freight",
            row.get("billed_base_freight"),
            row.get("contracted_base_rate", row.get("contracted_base_freight")),
            None,
            row.get("base_variance"),
            "money",
        ),
        (
            "Fuel surcharge",
            row.get("billed_fuel_surcharge"),
            row.get("contracted_fuel_surcharge", row.get("agreed_fuel_surcharge")),
            None,
            row.get("fuel_variance"),
            "money",
        ),
        (
            "Chargeable weight",
            billed_weight,
            expected_weight,
            scale_weight,
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
                    "Authorized (PO)": format_weight(authorized),
                    "Physical (WMS)": format_weight(physical),
                    "Variance": (
                        "N/A"
                        if _is_missing(delta)
                        else f"{delta:+,.0f} lb"
                    ),
                }
            )
    for label, column in (
        ("Truck + trailer + cargo gross weight", "gross_weight_lbs"),
        ("Empty truck + trailer tare weight", "tare_weight_lbs"),
    ):
        result.append(
            {
                "Component": label,
                "Invoiced (Carrier)": "—",
                "Authorized (PO)": "—",
                "Physical (WMS)": format_weight(row.get(column)),
                "Variance": "—",
            }
        )
    billed_items = row.get("accessorial_items", [])
    approved_items = row.get("approved_accessorial_items", [])
    legacy_allowance = _numeric_value(row.get("max_accessorial_allowance"))
    if (
        isinstance(billed_items, list)
        and billed_items
        and isinstance(approved_items, list)
        and (approved_items or legacy_allowance in (None, 0.0))
    ):
        billed_by_code: dict[str, dict[str, object]] = {}
        for item in billed_items:
            code = str(item.get("code", "")).strip().upper()
            entry = billed_by_code.setdefault(
                code,
                {
                    "description": str(item.get("description", "")).strip(),
                    "amount": 0.0,
                },
            )
            entry["amount"] = float(entry["amount"]) + float(item.get("amount", 0))
        approved_by_code: dict[str, float] = {}
        if isinstance(approved_items, list):
            for item in approved_items:
                code = str(item.get("code", "")).strip().upper()
                approved_by_code[code] = approved_by_code.get(code, 0.0) + float(
                    item.get("amount", 0)
                )
        accessorial_rows = []
        for code, item in billed_by_code.items():
            billed_amount = float(item["amount"])
            approved_amount = approved_by_code.get(code, 0.0)
            accessorial_rows.append(
                {
                    "Component": f"Accessorial — {code or 'UNKNOWN'}: {item['description']}",
                    "Invoiced (Carrier)": format_money(billed_amount),
                    "Authorized (PO)": format_money(approved_amount),
                    "Physical (WMS)": "—",
                    "Variance": format_money(max(0.0, billed_amount - approved_amount)),
                }
            )
        result[2:2] = accessorial_rows
    else:
        result.insert(
            2,
            {
                "Component": "Accessorial fees (legacy aggregate)",
                "Invoiced (Carrier)": format_money(row.get("billed_accessorial_fee")),
                "Authorized (PO)": format_money(row.get("max_accessorial_allowance")),
                "Physical (WMS)": "—",
                "Variance": format_money(row.get("accessorial_variance")),
            },
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
                "Variance detail": visible.apply(
                    lambda row: _exception_reasons(row, weight_tolerance_pct),
                    axis=1,
                ),
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
        st.subheader(
            f"{selected_id}: {selected['status']}. "
            f"{_exception_reasons(selected, weight_tolerance_pct)}"
        )
        with st.container():
            st.caption(
                f"{selected['carrier_name']} · PO {selected['po_number']} · "
                f"{selected['origin']} → {selected['destination']}"
            )
            invoice_pro = selected.get("pro_number")
            invoice_bol = selected.get("bol_number")
            st.caption(
                f"Carrier PRO {invoice_pro if isinstance(invoice_pro, str) and invoice_pro else 'unavailable'} "
                f"· BOL {invoice_bol if isinstance(invoice_bol, str) and invoice_bol else 'unavailable'} "
                f"· Invoice total due {format_money(selected.get('invoice_total_due', selected.get('total_amount')))}"
            )
            receipt_id = selected.get("receipt_id")
            scale_ticket_id = selected.get("scale_ticket_id")
            receiving_dock = selected.get("receiving_dock_door")
            receipt_details = [
                f"Receipt {receipt_id if isinstance(receipt_id, str) and receipt_id else 'ID unavailable'}",
                f"Scale ticket {scale_ticket_id if isinstance(scale_ticket_id, str) and scale_ticket_id else 'ID unavailable'}",
                f"Dock {receiving_dock if isinstance(receiving_dock, str) and receiving_dock else 'unavailable'}",
                f"Certified net {format_weight(selected.get('certified_net_scale_weight', selected.get('actual_scale_weight_lbs')))}",
            ]
            receipt_seal = selected.get("scale_certification_seal")
            if receipt_seal:
                receipt_details.append(f"Scale certification {receipt_seal}")
            st.caption(" · ".join(receipt_details))
            st.dataframe(
                _comparison_table(selected),
                use_container_width=True,
                hide_index=True,
            )
            osd_notes = selected.get("osd_notes")
            if isinstance(osd_notes, str) and osd_notes.strip():
                st.warning(f"Warehouse OS&D note: {osd_notes}")
            if not selected["receipt_found"]:
                st.warning("No warehouse receipt was found for this purchase order.")

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
                if st.button(
                    "Generate / Regenerate dispute memo",
                    key=f"generate_memo_{selected_id}",
                ):
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

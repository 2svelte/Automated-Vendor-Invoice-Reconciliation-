"""Streamlit dashboard for Supabase-backed three-way freight reconciliation."""

from __future__ import annotations

from pathlib import Path

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
from src.supabase_data import create_supabase_client, reconcile_supabase_data

PROJECT_ROOT = Path(__file__).resolve().parent
STATUSES = [TOUCHLESS_APPROVED, REVIEW_REQUIRED, DISCREPANCY_FLAGGED, REVIEW_INCOMPLETE]


@st.cache_resource
def get_supabase_client():
    return create_supabase_client()


def highlight_discrepancies(frame: pd.DataFrame):
    def row_style(row: pd.Series) -> list[str]:
        if row["status"] in {DISCREPANCY_FLAGGED, REVIEW_INCOMPLETE}:
            return ["background-color: #fde2e0; color: #781d18"] * len(row)
        return [""] * len(row)

    return frame.style.apply(row_style, axis=1)


def format_money(value: object) -> str:
    return "N/A" if pd.isna(value) else f"${value:,.2f}"


st.set_page_config(page_title="Freight Audit", page_icon="FA", layout="wide")
st.title("Freight Invoice Audit")
st.caption("Purchase orders, carrier invoices, and warehouse receipts reconciled from Supabase")

with st.sidebar:
    st.button("Refresh Supabase data", type="primary", use_container_width=True)

@st.fragment(run_every="15s")
def render_audit():
    try:
        audited = reconcile_supabase_data(get_supabase_client())
    except Exception as error:
        st.error(f"Could not load and audit Supabase data: {error}")
        return

    metrics = summarize(audited)
    kpis = st.columns(3)
    kpis[0].metric("Total audited", f"{metrics['invoice_count']:,}")
    kpis[1].metric("Potential overbill", format_money(metrics["prevented_overbill"]))
    kpis[2].metric("Touchless approval rate", f"{metrics['stp_rate']:.1%}")

    status_counts = audited["status"].value_counts()
    st.caption("  |  ".join(f"{status}: {int(status_counts.get(status, 0))}" for status in STATUSES))

    with st.container(horizontal=True):
        selected_statuses = st.multiselect("Status", STATUSES, default=STATUSES)
        carrier_options = sorted(audited["carrier_name"].dropna().astype(str).unique())
        selected_carriers = st.multiselect("Carrier", carrier_options, default=carrier_options)
        invoice_search = st.text_input("Invoice ID", placeholder="Filter invoices")

    filtered = audited[
        audited["status"].isin(selected_statuses)
        & audited["carrier_name"].astype("string").isin(selected_carriers)
    ]
    if invoice_search:
        filtered = filtered[
            filtered["invoice_number"].astype("string").str.contains(
                invoice_search, case=False, na=False
            )
        ]

    st.subheader("Three-way audit")
    display_columns = [
        "invoice_number", "carrier_name", "po_number", "status", "base_variance",
        "fuel_variance", "accessorial_variance", "weight_variance_lbs", "total_overbill",
        "receipt_found",
    ]
    display = filtered[[column for column in display_columns if column in filtered]].copy()
    for column in ["base_variance", "fuel_variance", "accessorial_variance", "total_overbill"]:
        if column in display:
            display[column] = display[column].map(format_money)
    if "weight_variance_lbs" in display:
        display["weight_variance_lbs"] = display["weight_variance_lbs"].map(
            lambda value: "N/A" if pd.isna(value) else f"{value:,.0f} lb"
        )
    st.dataframe(highlight_discrepancies(display), use_container_width=True, hide_index=True)

    if not filtered.empty:
        selected_id = st.selectbox("Invoice breakdown", filtered["invoice_number"].tolist())
        selected = audited.loc[audited["invoice_number"].eq(selected_id)].iloc[0]
        with st.expander(f"{selected_id} details", expanded=True):
            details = st.columns(4)
            details[0].metric("Base variance", format_money(selected["base_variance"]))
            details[1].metric("Fuel variance", format_money(selected["fuel_variance"]))
            details[2].metric("Accessorial variance", format_money(selected["accessorial_variance"]))
            details[3].metric("Total overbill", format_money(selected["total_overbill"]))
            billed_weight = selected["billed_weight_lbs"]
            receipt_weight = selected["actual_scale_weight_lbs"]
            st.write(
                "Billed weight: "
                f"{billed_weight:,.0f} lb" if pd.notna(billed_weight) else "Billed weight: N/A"
            )
            st.write(
                "Receipt scale weight: "
                f"{receipt_weight:,.0f} lb" if pd.notna(receipt_weight) else "Receipt scale weight: N/A"
            )
            if selected["accessorial_variance"] > 0:
                st.warning(
                    "The source invoice provides one aggregate accessorial amount, not a fee-type breakdown."
                )
            st.write(f"Audit status: **{selected['status']}**")
    else:
        st.info("No invoices match the current filters.")

    if st.button("Export discrepancy queue for Power Automate"):
        queue = write_discrepancy_queue(
            audited, PROJECT_ROOT / "automation" / "discrepancy_queue.json"
        )
        st.success(f"Exported {len(queue)} exception(s).")


render_audit()

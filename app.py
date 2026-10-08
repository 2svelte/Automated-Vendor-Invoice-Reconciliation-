"""Streamlit dashboard for Supabase-backed freight invoice reconciliation."""

from __future__ import annotations

import os

import pandas as pd
import streamlit as st

from src.supabase_audit import (
    AUTO_APPROVED,
    DISPUTED,
    REQUIRES_REVIEW,
    reconcile_supabase_records,
)


@st.cache_resource
def get_supabase_client():
    from dotenv import load_dotenv
    from supabase import create_client

    load_dotenv()
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_KEY")
    if not supabase_url or not supabase_key:
        raise ValueError("Set SUPABASE_URL and SUPABASE_KEY in the environment or a .env file.")
    return create_client(supabase_url, supabase_key)


def fetch_table(client, table_name: str, columns: list[str]) -> pd.DataFrame:
    page_size = 1000
    rows = []
    offset = 0
    while True:
        response = client.table(table_name).select("*").range(offset, offset + page_size - 1).execute()
        page = response.data or []
        rows.extend(page)
        if len(page) < page_size:
            break
        offset += page_size
    return pd.DataFrame(rows, columns=None if rows else columns)


def highlight_discrepancies(frame: pd.DataFrame):
    def row_style(row: pd.Series) -> list[str]:
        color = "background-color: #fde2e0; color: #781d18" if row["status"] == DISPUTED else ""
        return [color] * len(row)

    return frame.style.apply(row_style, axis=1)


st.set_page_config(page_title="Freight Audit", page_icon="FA", layout="wide")
st.title("Freight Invoice Audit")
st.caption("Carrier bills matched against internal dispatch baselines")

with st.sidebar:
    st.subheader("Audit tolerances")
    amount_tolerance = st.number_input("Amount buffer ($)", min_value=0.0, value=1.0, step=0.25)
    weight_tolerance = st.number_input("Weight review buffer (lb)", min_value=0.0, value=50.0, step=10.0)
    rate_match_tolerance = st.number_input("Fallback rate match ($)", min_value=0.0, value=1.0, step=0.25)
    refresh = st.button("Refresh Supabase data", type="primary", use_container_width=True)

if refresh:
    st.cache_resource.clear()

try:
    supabase = get_supabase_client()
    invoices = fetch_table(
        supabase,
        "carrier_invoices",
        ["invoice_id", "carrier_name", "shipment_id", "billed_weight", "base_rate", "fuel_surcharge", "accessorial_fees"],
    )
    dispatches = fetch_table(
        supabase,
        "internal_dispatches",
        ["shipment_id", "carrier_name", "expected_weight", "contracted_base_rate", "contracted_fuel_surcharge", "approved_accessorials", "expected_total"],
    )
    audited = reconcile_supabase_records(
        invoices,
        dispatches,
        amount_tolerance=amount_tolerance,
        weight_tolerance=weight_tolerance,
        rate_match_tolerance=rate_match_tolerance,
    )
except Exception as error:
    st.error(f"Could not load and audit Supabase data: {error}")
    st.stop()

total_audited = len(audited)
disputed = audited[audited["status"].eq(DISPUTED)]
net_flagged = disputed["total_variance"].fillna(0).sum()
dispute_rate = len(disputed) / total_audited if total_audited else 0.0

kpis = st.columns(3)
kpis[0].metric("Total audited", f"{total_audited:,}")
kpis[1].metric("Net discrepancies flagged", f"${net_flagged:,.2f}")
kpis[2].metric("Dispute rate", f"{dispute_rate:.1%}")

status_counts = audited["status"].value_counts()
st.caption(
    "  |  ".join(
        f"{status}: {int(status_counts.get(status, 0))}"
        for status in (AUTO_APPROVED, DISPUTED, REQUIRES_REVIEW)
    )
)

with st.container(horizontal=True):
    selected_statuses = st.multiselect(
        "Status",
        [AUTO_APPROVED, DISPUTED, REQUIRES_REVIEW],
        default=[AUTO_APPROVED, DISPUTED, REQUIRES_REVIEW],
    )
    carrier_options = sorted(audited["carrier_name"].dropna().astype(str).unique())
    selected_carriers = st.multiselect("Carrier", carrier_options, default=carrier_options)
    invoice_search = st.text_input("Invoice ID", placeholder="Filter invoices")

filtered = audited[
    audited["status"].isin(selected_statuses)
    & audited["carrier_name"].astype("string").isin(selected_carriers)
]
if invoice_search:
    filtered = filtered[filtered["invoice_id"].astype("string").str.contains(invoice_search, case=False, na=False)]

st.subheader("Audited invoices")
display_columns = [
    "invoice_id", "carrier_name", "shipment_id", "dispatch_shipment_id", "status",
    "base_rate_variance", "weight_variance", "accessorial_overage", "total_variance",
]
display = filtered[[column for column in display_columns if column in filtered]].copy()
for column in ["base_rate_variance", "accessorial_overage", "total_variance"]:
    if column in display:
        display[column] = display[column].map(lambda value: "" if pd.isna(value) else f"${value:,.2f}")
if "weight_variance" in display:
    display["weight_variance"] = display["weight_variance"].map(lambda value: "" if pd.isna(value) else f"{value:,.0f} lb")
st.dataframe(highlight_discrepancies(display), use_container_width=True, hide_index=True)

if not audited.empty:
    invoice_ids = filtered["invoice_id"].tolist()
    if invoice_ids:
        selected_id = st.selectbox("Invoice breakdown", invoice_ids)
        selected = audited.loc[audited["invoice_id"].eq(selected_id)].iloc[0]
        with st.expander(f"{selected_id} details", expanded=True):
            details = st.columns(4)
            details[0].metric("Base rate variance", f"${selected['base_rate_variance']:,.2f}" if pd.notna(selected["base_rate_variance"]) else "N/A")
            details[1].metric("Weight variance", f"{selected['weight_variance']:,.0f} lb" if pd.notna(selected["weight_variance"]) else "N/A")
            details[2].metric("Accessorial overage", f"${selected['accessorial_overage']:,.2f}" if pd.notna(selected["accessorial_overage"]) else "N/A")
            details[3].metric("Total variance", f"${selected['total_variance']:,.2f}" if pd.notna(selected["total_variance"]) else "N/A")
            if selected["accessorial_overage"] > amount_tolerance:
                st.warning(
                    f"Unapproved accessorial overage: ${selected['accessorial_overage']:,.2f}. "
                    "The source invoice provides one aggregate accessorial amount, not a fee-type breakdown."
                )
            st.write(f"Audit status: **{selected['status']}**")
else:
    st.info("No invoices match the current filters.")

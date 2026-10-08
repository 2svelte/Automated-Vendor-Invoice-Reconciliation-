# Automated Freight Invoice Reconciliation

A Streamlit freight invoice audit dashboard backed by Supabase. The ingestion path extracts inbound PDF bills directly into PostgreSQL, and the dashboard compares carrier invoices with internal dispatch baselines.

## Quick start

Requires Python 3.10 or newer.

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
streamlit run app.py
```

Open the local Streamlit URL printed by the command. Configure Supabase as described below before starting the dashboard.

## Supabase setup

The schema is tracked in `supabase/migrations/`. After `supabase init` and `supabase link --project-ref <project-ref>`, preview and apply migrations with `supabase db push --dry-run` and `supabase db push`. Set `SUPABASE_URL` and `SUPABASE_KEY` in the repo-root `.env` file for the app. Install dependencies with `python -m pip install -r requirements.txt`.

The first migration creates `carrier_invoices` and `internal_dispatches`. The next adds `purchase_orders` and `warehouse_goods_receipts`, and adds the canonical invoice columns used by the three-way engine. Before running the dashboard, ensure the purchase order and receipt tables are populated in Supabase from your ERP and warehouse sources.

Power Automate Desktop is the invoice ingestion trigger. After it saves an attachment to `data/inbound_outlook_invoices`, run this command from the repository root to parse the PDFs and upsert them into `carrier_invoices`:

```powershell
py -3.10 -c "from src.extractor import sync_inbound_pdfs_to_supabase; sync_inbound_pdfs_to_supabase('data/inbound_outlook_invoices')"
```

The command scans every PDF in the folder each time it runs; upserts use `invoice_id`, so rerunning it updates existing invoices rather than creating duplicate rows. The dashboard only reads Supabase and refreshes the three-way reconciliation every 15 seconds. The source invoice format provides accessorial fees as one aggregate amount, so the dashboard reports the unapproved amount without assuming a fee type.

For live OpenAI dispute drafting, copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` and add your own `OPENAI_API_KEY`. The app reads that Streamlit secret at draft time. Without a key, the console produces a clearly labeled deterministic draft using only reconciliation facts. Never commit a real API key, including to the example file.

## Legacy CSV fixtures

The CSV files are not used by the live dashboard. They remain as historical examples and local test fixtures; the running app reads purchase orders, invoices, and warehouse receipts from Supabase and passes those frames to the deterministic three-way reconciliation engine.

- `data/erp_purchase_orders.csv`: `po_number`, vendor and route identifiers, contracted base freight, agreed fuel surcharge, and maximum authorized accessorial allowance.
- `data/carrier_invoices_raw.csv`: invoice and PO identifiers, billed base freight, billed fuel surcharge, billed accessorial fee, billed weight, and invoice date.
- `data/warehouse_goods_receipts.csv`: PO identifier, delivery timestamp, dock signature, and actual scale weight.

Upstream percentage-based fuel data must be normalized into the monetary `agreed_fuel_surcharge` value before reconciliation.

## Business rules

- `base_variance = billed_base_freight - contracted_base_freight`.
- `fuel_variance = billed_fuel_surcharge - agreed_fuel_surcharge`.
- `accessorial_variance = max(0, billed_accessorial_fee - max_accessorial_allowance)`.
- `total_overbill` is the sum of positive base, fuel, and accessorial variances.
- `variance_pct` uses `contracted_base_freight + agreed_fuel_surcharge + max_accessorial_allowance` as its denominator.
- A billed weight up to 2% above the dock scale weight passes the weight check. Higher overage is flagged, but weight has no invented dollar rate.
- Missing receipts produce `REVIEW_INCOMPLETE` and cannot be touchlessly approved.
- Complete records at or below 1% variance are `TOUCHLESS_APPROVED`.
- Complete records above 5% variance, or beyond the weight tolerance, are `DISCREPANCY_FLAGGED`.
- Other complete records are `REVIEW_REQUIRED`.

The deterministic engine owns all amounts and statuses. The AI layer only drafts language from a structured exception record.

## Power Automate integration

The dashboard exports exceptions to `automation/discrepancy_queue.json` using **Export discrepancy queue for Power Automate**. The queue contains invoice/PO identifiers, status, variance amounts, receipt state, and the `approve_overcharge` / `send_ai_dispute` actions. Invoice PDFs are watched separately in `data/inbound_outlook_invoices` while Streamlit is running.

`automation/power_automate_flow_spec.json` documents the machine-readable trigger and routing contract. `automation/power_automate_flow_spec.md` provides exact Power Automate Desktop and cloud setup steps. `automation/teams_card_template.json` is the Adaptive Card payload contract. These files describe the integration; Power Automate execution requires deployment in the target Microsoft tenant.

## Verification

```powershell
py -3.10 -m pytest -q
py -3.10 -m compileall src app.py
```

The generated demo data intentionally contains clean invoices, a review-band variance, material fuel/accessorial overcharges, a weight anomaly, and a missing receipt so each operational state can be exercised locally.

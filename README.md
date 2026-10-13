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

The migrations create `carrier_invoices`, `internal_dispatches`, `purchase_orders`, and `warehouse_goods_receipts`. They add canonical logistics-document fields and the audit workbench's append-only `invoice_audit_events` trail, written transactionally by a database trigger whenever an auditor changes a resolution. The logistics-document details migration adds billing/remittance and rating fields to invoices, trade terms/equipment/schedule fields to purchase orders, and driver/seal/scale/yard/receiving fields to warehouse receipts. It also sets visibly fictional `DEMO` values on existing PO-1004/PO-1007 and matching receipt rows, if those rows exist. Apply all migrations before using the workbench.

Power Automate Desktop is the invoice ingestion trigger. After it saves an attachment to `data/inbound_outlook_invoices`, run this command from the repository root to parse the PDFs and upsert them into `carrier_invoices`:

```powershell
py -3.10 -c "from src.extractor import sync_inbound_pdfs_to_supabase; sync_inbound_pdfs_to_supabase('data/inbound_outlook_invoices')"
```

The command scans every PDF in the folder each time it runs; upserts use `invoice_id`, so rerunning it updates existing invoices rather than creating duplicate rows. After applying the logistics-document details migration, re-import invoices with `py -3.10 -c "from src.extractor import sync_inbound_pdfs_to_supabase; print(sync_inbound_pdfs_to_supabase('data/inbound_outlook_invoices'))"` from the repository root. This parses the PDFs already in that folder and upserts the additional invoice fields; it does not alter the PDF files or require changing CSV fixtures. Carrier PDFs include invoice and remit-to parties, payment terms/due date, transit dates, freight classification and tariff rating, handling units, charge details, and invoice/PO/BOL references. The extractor writes the parsed document fields and itemized accessorial JSON to `carrier_invoices`. The dashboard refreshes the three-way reconciliation every 15 seconds. Reviewers can mark an exception under review, dispute it, approve it with an override, or request a revised scale ticket; each action records its status, timestamp, and required resolution note in Supabase and creates an audit-history event. The workbench does not request or store reviewer names. Resolved exceptions leave the active queue and remain visible in audit history. The dispute memo is downloadable for vendor communication.

Fuel tolerance and weight buffer are adjustable in the Streamlit sidebar. The displayed overbill remains the full calculated amount; fuel tolerance affects exception classification only. Itemized invoice accessorials are compared by code and amount with `purchase_orders.approved_accessorial_items`; unauthorized codes or amounts appear as actionable findings (for example, an unapproved `DET` detention charge). Migrated legacy records with only aggregate accessorial allowances continue to use the aggregate comparison.

For live OpenAI dispute drafting, copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` and add your own `OPENAI_API_KEY`. Use **Generate / Regenerate dispute memo** on a flagged invoice to request a draft; the selected reconciliation record and sender details from `data/company_profile.json` are supplied to OpenAI. Review the generated text before sending it. If the API key is missing or OpenAI is unavailable, the app displays “AI is currently down” and does not create a fallback letter. Generated sample carrier invoices include the company as the bill-to contact. Never commit a real API key, including to the example file.

## Legacy CSV fixtures

The CSV files are not used by the live dashboard. They remain as historical examples and local test fixtures; the running app reads purchase orders, invoices, and warehouse receipts from Supabase and passes those frames to the deterministic three-way reconciliation engine.

- `data/erp_purchase_orders.csv`: PO/vendor/carrier and route identifiers, `contracted_expected_weight`, contracted rates, `approved_accessorial_items` (JSON), and authorized accessorial codes.
- `data/carrier_invoices_raw.csv`: PDF-extracted invoice/PO/BOL identifiers, `billed_chargeable_weight`, linehaul and fuel amounts, itemized `accessorial_items` (JSON), total due, and invoice date.
- `data/warehouse_goods_receipts.csv`: receipt/PO/BOL and scale-ticket identifiers, `received_timestamp`, receiving dock and trailer details, gross/tare weights, `certified_net_scale_weight`, certification seal, and OS&D notes. These PO and receipt records seed their corresponding Supabase tables; live reconciliation reads those tables rather than the CSV files.

Upstream percentage-based fuel data must be normalized into the monetary `agreed_fuel_surcharge` value before reconciliation.

## Business rules

- `base_variance = billed_base_freight - contracted_base_freight`.
- `fuel_variance = billed_fuel_surcharge - agreed_fuel_surcharge`.
- `accessorial_variance` sums per-code positive overages: billed amount less the PO allowance for that code. A code not listed on the PO has a zero allowance.
- `total_overbill` is the sum of positive base, fuel, and accessorial variances.
- `variance_pct` uses `contracted_base_freight + agreed_fuel_surcharge + max_accessorial_allowance` as its denominator.
- By default, a billed weight up to 2% above the dock scale weight passes the weight check; the workbench lets an auditor adjust the percentage. Higher overage is flagged, but weight has no invented dollar rate.
- By default, fuel variances up to $1 are within tolerance. The workbench lets an auditor adjust the dollar tolerance; the reported overbill remains unchanged.
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

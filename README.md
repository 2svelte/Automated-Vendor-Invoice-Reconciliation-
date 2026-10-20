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

The migrations create `carrier_invoices`, `purchase_orders`, and `warehouse_goods_receipts`. The historical initial schema also created `internal_dispatches`; a later migration drops it because the workbench reconciles directly against purchase orders, invoices, and receipts. Migrations add canonical logistics-document fields, an append-only `invoice_audit_events` trail, and a dedicated `dispute_dispatches` outbox table. The latest migrations allow duplicate invoice numbers to be captured for detection and add attachment/error metadata for the dispute voucher and PAD dispatch. The invoice is marked `DISPUTED` when its dispatch is queued; the 30-day rebuttal deadline starts only after Outlook delivery is confirmed. For this project demo, existing and new records default `carrier_email` to `nadiaravindra07@gmail.com`; replace it with each verified carrier's real billing address before sending production disputes. The outbox migration also sets visibly fictional `DEMO` values on existing PO-1004/PO-1007 and matching receipt rows, if those rows exist. Apply all migrations before using the workbench.

Power Automate Desktop is the invoice ingestion trigger. After it saves an attachment to `data/inbound_outlook_invoices`, run this command from the repository root to parse the PDFs and upsert them into `carrier_invoices`:

```powershell
py -3.10 -c "from src.extractor import sync_inbound_pdfs_to_supabase; sync_inbound_pdfs_to_supabase('data/inbound_outlook_invoices')"
```

The command scans every PDF in the folder each time it runs. The importer stores a source-document hash so reprocessing the same PDF remains idempotent, while a different PDF reusing the same invoice number is retained under a unique invoice ID. The reconciliation loader detects duplicate invoice numbers and repeated carrier + BOL + PO triplets, marks later bills `DUPLICATE_BILLING_BLOCKED`, and identifies the original invoice. Duplicate bills cannot be approved for payment.

The dashboard refreshes the three-way reconciliation every 15 seconds. For each invoice it shows total invoiced, disputed/withheld amount, and net approved remittance. Its AIP fuel audit card shows the departure week's diesel benchmark, contract peg, calculated allowance, and billed-versus-authorized verdict. Scale weights in the reconciliation table and dispute voucher are displayed in kilograms. Dispute memos include a deterministic short-pay statement and an itemized AIP fuel dispute line when applicable. Reviewers can mark an exception under review, approve it with an override, or request a revised scale ticket. The audit trail records lifecycle changes by invoice ID. From a reviewed memo, download a one-page PDF voucher or open the **Send As Email** composer. The voucher includes invoice variances, certified scale-ticket data, receiving details, receiver signature, and OS&D notes. The composer reads **To** from the invoice's `carrier_email` field and displays the fixed **From** address `nadiaravindra07@outlook.com`; the verified PDF is attached by default. On **Confirm & Send**, the app atomically queues the dispatch in Supabase as `PENDING` and sets the invoice to `DISPUTED`, then closes the composer. Power Automate Desktop polls Supabase for pending dispatches, sends them via Outlook, and updates their status to `SENT` or `FAILED`. The database starts the 30-day rebuttal deadline after the flow confirms successful delivery.

The current demo's `carrier_email` value is your Gmail address, so dispatches go to your Gmail inbox—not to freight companies—until you replace each value with the matching carrier's verified address. Outlook can generally send email to Gmail recipients, but your Microsoft 365 tenant's external-recipient policy may restrict it. Power Automate must be authorized to send as `nadiaravindra07@outlook.com`; the From value is not a way to bypass Outlook permissions.

The AIP fuel tolerance and weight buffer are adjustable in the Streamlit sidebar. Fuel variances at or below the tolerance are accepted as rounding differences; amounts above it trigger an overcharge exception and are withheld. Other positive billed-minus-authorized variances are flagged and withheld, while underbilled lines remain favorable savings and do not offset overcharges on other lines. Itemized invoice accessorials are compared by code and amount with `purchase_orders.approved_accessorial_items`; unauthorized codes or amounts appear as actionable findings (for example, an unapproved `DET` detention charge). Migrated legacy records with only aggregate accessorial allowances continue to use the aggregate comparison.

For live OpenAI dispute drafting, copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` and add your `OPENAI_API_KEY`. Use **Generate / Regenerate dispute memo** on a flagged invoice to request a draft; the selected reconciliation record and sender details from `data/company_profile.json` are supplied to OpenAI. Review the generated text before sending it. If the API key is missing or OpenAI is unavailable, the app displays “AI is currently down” and does not create a fallback letter. Generated sample carrier invoices include the company as the bill-to contact.

## Legacy CSV fixtures

The CSV files are not used by the live dashboard. They remain as historical examples and local test fixtures; the running app reads purchase orders, invoices, and warehouse receipts from Supabase and passes those frames to the deterministic three-way reconciliation engine.

- `data/erp_purchase_orders.csv`: PO/vendor/carrier and route identifiers, `contracted_expected_weight`, contracted rates, `approved_accessorial_items` (JSON), and authorized accessorial codes.
- `data/carrier_invoices_raw.csv`: PDF-extracted invoice/PO/BOL identifiers, `billed_chargeable_weight`, linehaul and fuel amounts, itemized `accessorial_items` (JSON), total due, and invoice date.
- `data/warehouse_goods_receipts.csv`: receipt/PO/BOL and scale-ticket identifiers, `received_timestamp`, receiving dock and trailer details, gross/tare weights, `certified_net_scale_weight`, certification seal, and OS&D notes. These PO and receipt records seed their corresponding Supabase tables; live reconciliation reads those tables rather than the CSV files.

The AIP weekly diesel benchmarks are recorded in `src/reconciliation_engine.py` in AUD/L. The shipment's pickup week selects the Monday index date; weeks without a published entry use the documented $1.95/L default. The contractual allowance applies 0.5% of PO base freight for each complete 5¢/L step above the $1.50/L base peg.

## Business rules

- `base_variance = billed_base_freight - contracted_base_freight`.
- `authorized_fuel_surcharge` is calculated from PO base freight and the shipment-week AIP index; `fuel_variance = billed_fuel_surcharge - authorized_fuel_surcharge`, so an overcharge is positive.
- Positive base and accessorial variances are overcharges. A fuel variance greater than the adjustable `AIP Fuel Tolerance ($ AUD)` is classified as `DISCREPANCY_FLAGGED` with root cause `UNAUTHORIZED_FUEL_CREEP`; values at or below the threshold are accepted as rounding differences.
- `accessorial_variance` is billed minus authorized across accessorial lines; `accessorial_overcharge` sums only positive per-code differences. A code not listed on the PO has a zero allowance.
- `disputed_variance` is the sum of positive base and accessorial overcharges plus fuel overcharges above tolerance. Favorable underbills do not reduce this amount.
- `total_invoiced` is the sum of billed base freight, fuel, and accessorial line items; `approved_remittance = total_invoiced - disputed_amount`.
- `variance_pct` uses `contracted_base_freight + authorized_fuel_surcharge + max_accessorial_allowance` as its denominator.
- By default, a billed weight up to 2% above the dock scale weight passes the weight check; the workbench lets an auditor adjust the percentage. Higher overage is flagged, but weight has no invented dollar rate.
- Missing receipts produce `REVIEW_INCOMPLETE` and cannot be touchlessly approved.
- Complete records with no positive disputed variance and within the weight tolerance are `TOUCHLESS_APPROVED`, including underbilled loads and fuel differences within tolerance.
- Any positive disputed variance is `DISCREPANCY_FLAGGED`. Weight-only exceptions are `REVIEW_REQUIRED`; weight differences do not create an invented dollar overcharge.

The PO-1007 fixture retains the repository's existing invoice and scale values. For its $2,750 contracted base and $285 billed fuel, the $1.95/L default benchmark authorizes 4.5% ($123.75) under the specified 0.5%-per-step policy. CSV and Supabase source weight values retain their legacy pound-based field names; the user-facing reconciliation and voucher convert them to kilograms.

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

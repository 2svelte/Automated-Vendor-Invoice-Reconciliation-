# Power Automate Handshake Runbook

Power Automate owns mailbox/folder monitoring, Outlook delivery, and operational notifications; Python owns deterministic reconciliation and queue formatting.

## Prerequisites

- The project folder must be accessible to Power Automate Desktop, or the files must be exposed through OneDrive/SharePoint for a cloud flow.
- Run the Streamlit app from the project root so paths resolve to `data/`, `inbox_drop/`, and `automation/`.
- For cloud flows, replace local file-system actions with OneDrive or SharePoint actions and use the same JSON schema.

## Inbound flow: invoice ingestion

1. Create or use the project folder `inbox_drop/`.
2. For a local demo, configure Power Automate Desktop with **Wait for file** or a folder watcher for a new `.csv` file in `inbox_drop/`.
3. For an Outlook flow, use **When a new email arrives (V3)** on the shared freight-invoices mailbox and inspect the attachment extension.
4. Save or move the invoice attachment into `inbox_drop/`.
5. Move or copy the file to `data/carrier_invoices_raw.csv`. The Streamlit app also recognizes the newest `.csv` in `inbox_drop/` and copies it into that canonical path for a local demonstration.
6. Ensure `data/erp_purchase_orders.csv` and `data/warehouse_goods_receipts.csv` are present. Select **Reconcile invoices** in Streamlit, or schedule the app/session to run the reconciliation step.
7. The app writes the deterministic result to `data/reconciled_audit_log.csv`.

The invoice CSV must contain the columns documented in `README.md`. Do not let the LLM normalize or calculate invoice values.

## Outbound flow: discrepancy escalation

### Option A: Power Automate Desktop local demo

1. Add a **Wait for file creation** action targeting:
   `automation/discrepancy_queue.json`
2. Add **Read text from file** for the created file.
3. Add **Parse JSON** using an array schema derived from this example object:

```json
{
  "invoice_number": "INV-1004",
  "po_number": "PO-1004",
  "carrier_name": "Northstar Freight",
  "status": "DISCREPANCY_FLAGGED",
  "total_overbill": 99.8,
  "variance_pct": 0.068,
  "base_variance": 0,
  "fuel_variance": 0,
  "accessorial_variance": 100,
  "weight_variance_lbs": 0,
  "receipt_found": true,
  "actions": ["approve_overcharge", "send_ai_dispute"]
}
```

4. Add an **Apply to each** over the parsed array.
5. For a local demonstration, use **Display message** or a custom input dialog with the carrier, PO, overbill, and variance fields.
6. For a real escalation, replace that action with **Post adaptive card in a chat or channel** and use `automation/teams_card_template.json` as the card contract.
7. Bind the card template placeholders to the current parsed item. Preserve `invoice_number` and `po_number` in the action data so the response can be correlated.
8. Branch on the submitted action:
   - `approve_overcharge`: record manager approval and release the invoice to AP.
   - `send_ai_dispute`: call the dispute-drafting endpoint or operator workflow, then send the reviewed dispute email and record the credit memo request.
9. Write the action, actor, timestamp, and invoice/PO identifiers to the system of record.

### Outlook dispute dispatch from the Streamlit composer

1. Apply all Supabase migrations, including `20261016120000_dispute_evidence_lifecycle_and_duplicate_billing.sql`.
2. In the Streamlit workbench, generate and review the short-pay memo and one-page evidence voucher, choose **Send As Email**, and submit **Confirm & Send**. In one database transaction, the app inserts a row in `dispute_dispatches` with the invoice ID, carrier, recipients, subject, memo, optional base64 PDF and filename, and `email_dispatch_status = PENDING`. The invoice does not enter `DISPUTED` until delivery is confirmed.
3. Configure a scheduled cloud flow to run every 1–2 minutes and retrieve pending messages:
   `GET https://<project-ref>.supabase.co/rest/v1/dispute_dispatches?email_dispatch_status=eq.PENDING`
   Include the Supabase API key and an appropriately scoped authorization token in Power Automate's secure connection/configuration; do not put credentials in the Streamlit app or repository.
4. In **Apply to each**, add the Microsoft 365 Outlook **Send an email (V2)** action. Map `dispute_to_email` to To, `dispute_cc_email` to CC, `dispute_subject` to Subject, and `dispute_memo_body` to Body. When `dispute_evidence_pdf_base64` is not empty, map `dispute_evidence_filename` to the attachment name and `base64ToBinary(dispute_evidence_pdf_base64)` to attachment content. Set From (Send as) to `nadiaravindra07@outlook.com`; the Outlook connection must have permission to send as that address.
5. After successful delivery, PATCH the same row using its `dispatch_id`:
   `PATCH https://<project-ref>.supabase.co/rest/v1/dispute_dispatches?dispatch_id=eq.<dispatch_id>`
   `{"email_dispatch_status":"SENT","dispatched_at":"<UTC timestamp>"}`
   This confirmation starts the 30-day rebuttal window; the database sets the invoice to `DISPUTED`, records `dispute_date`, and calculates `rebuttal_deadline`.
   On a terminal send error, PATCH the same endpoint with `{"email_dispatch_status":"FAILED"}` so the reviewer can correct details and queue a retry.
6. The Streamlit workbench polls Supabase every 15 seconds, prevents another queue while status is `PENDING` or `SENT`, and confirms delivery when the flow reports `SENT`. Reviewers can advance the invoice through `REBUTTAL_RECEIVED`, `CLOSED_RESOLVED` (credit memo issued), and `CLOSED_OVERRIDDEN` (dispute conceded); the workbench displays the SLA countdown.

Duplicate invoices are detected by identical invoice number or by carrier + BOL + PO. `DUPLICATE_BILLING_BLOCKED` records show the original invoice ID and must not be routed to `approve_overcharge`.

Configure retries and concurrency control in the flow so one pending row is not sent more than once. Restrict Supabase credentials to the minimum required operations and protect the flow's HTTP actions.

The project demo defaults the `carrier_email` column on invoices, purchase orders, and receipts to `nadiaravindra07@gmail.com`. This is a test destination, not a real carrier contact; update these rows to verified carrier addresses before production use. Outlook-to-Gmail delivery is usually supported, subject to the Microsoft 365 tenant's external-recipient policy.

The Streamlit app currently queues messages in Supabase and does not POST to a Power Automate HTTP-trigger URL. Use scheduled polling unless an HTTP invocation is added separately.

### Option B: Power Automate cloud flow

1. Use **When a file is created** in OneDrive/SharePoint for the synchronized `automation` folder.
2. Add **Get file content** for `discrepancy_queue.json`.
3. Add **Parse JSON** with an array schema matching the fields above.
4. Add **Apply to each** and **Post adaptive card in a chat or channel** using the Teams card contract.
5. Add the approval/dispute branches described in `automation/power_automate_flow_spec.json`.
6. Configure retry handling and move processed queue files to an archive folder so the same exception is not sent repeatedly.

## Streamlit action

In the dashboard, open **Workflow export** and select **Export Discrepancy Queue for Power Automate**. The app writes the complete current exception array to `automation/discrepancy_queue.json` and also offers a download for inspection. The queue includes `REVIEW_INCOMPLETE` records because missing proof of delivery requires operational review even when no monetary variance is available.

## Security and operational notes

- Do not commit `.streamlit/secrets.toml` or API keys.
- Treat the queue as an operational handoff, not as an approval record. The manager action must be written back by Power Automate.
- Keep the deterministic reconciliation audit log alongside the queue for traceability.
- Use an archive/processed strategy in production to avoid duplicate alerts.

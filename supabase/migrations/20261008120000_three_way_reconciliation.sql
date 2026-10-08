CREATE TABLE IF NOT EXISTS public.purchase_orders (
    po_number TEXT PRIMARY KEY,
    vendor_id TEXT NOT NULL,
    carrier_name TEXT NOT NULL,
    origin TEXT NOT NULL,
    destination TEXT NOT NULL,
    contracted_base_freight NUMERIC NOT NULL,
    agreed_fuel_surcharge NUMERIC NOT NULL,
    max_accessorial_allowance NUMERIC NOT NULL
);

CREATE TABLE IF NOT EXISTS public.warehouse_goods_receipts (
    po_number TEXT PRIMARY KEY,
    delivery_timestamp TIMESTAMPTZ,
    dock_signature TEXT,
    actual_scale_weight_lbs NUMERIC
);

ALTER TABLE public.carrier_invoices
    ADD COLUMN IF NOT EXISTS invoice_number TEXT,
    ADD COLUMN IF NOT EXISTS po_number TEXT,
    ADD COLUMN IF NOT EXISTS billed_base_freight NUMERIC,
    ADD COLUMN IF NOT EXISTS billed_fuel_surcharge NUMERIC,
    ADD COLUMN IF NOT EXISTS billed_accessorial_fee NUMERIC,
    ADD COLUMN IF NOT EXISTS billed_weight_lbs NUMERIC,
    ADD COLUMN IF NOT EXISTS invoice_date DATE;

UPDATE public.carrier_invoices
SET invoice_number = COALESCE(invoice_number, invoice_id),
    po_number = COALESCE(po_number, shipment_id),
    billed_base_freight = COALESCE(billed_base_freight, base_rate),
    billed_fuel_surcharge = COALESCE(billed_fuel_surcharge, fuel_surcharge),
    billed_accessorial_fee = COALESCE(billed_accessorial_fee, accessorial_fees),
    billed_weight_lbs = COALESCE(billed_weight_lbs, billed_weight),
    invoice_date = COALESCE(invoice_date, issue_date);

ALTER TABLE public.carrier_invoices
    ALTER COLUMN invoice_number SET NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS carrier_invoices_invoice_number_key
    ON public.carrier_invoices (invoice_number);
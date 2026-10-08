CREATE TABLE IF NOT EXISTS public.carrier_invoices (
    invoice_id TEXT PRIMARY KEY,
    carrier_name TEXT NOT NULL,
    issue_date DATE,
    shipment_id TEXT,
    billed_weight NUMERIC,
    base_rate NUMERIC,
    fuel_surcharge NUMERIC,
    accessorial_fees NUMERIC DEFAULT 0.0,
    total_amount NUMERIC GENERATED ALWAYS AS (
        base_rate + fuel_surcharge + accessorial_fees
    ) STORED,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public.internal_dispatches (
    shipment_id TEXT PRIMARY KEY,
    carrier_name TEXT,
    expected_weight NUMERIC,
    contracted_base_rate NUMERIC,
    contracted_fuel_surcharge NUMERIC,
    approved_accessorials NUMERIC DEFAULT 0.0,
    expected_total NUMERIC
);
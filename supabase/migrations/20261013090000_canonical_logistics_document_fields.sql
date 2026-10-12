ALTER TABLE public.carrier_invoices
    ADD COLUMN IF NOT EXISTS bol_number TEXT,
    ADD COLUMN IF NOT EXISTS pro_number TEXT,
    ADD COLUMN IF NOT EXISTS billed_chargeable_weight NUMERIC,
    ADD COLUMN IF NOT EXISTS accessorial_items JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS invoice_total_due NUMERIC;

UPDATE public.carrier_invoices
SET billed_chargeable_weight = COALESCE(billed_chargeable_weight, billed_weight_lbs, billed_weight),
    invoice_total_due = COALESCE(
        invoice_total_due,
        billed_base_freight + billed_fuel_surcharge + billed_accessorial_fee
    );

ALTER TABLE public.purchase_orders
    ADD COLUMN IF NOT EXISTS authorized_carrier TEXT,
    ADD COLUMN IF NOT EXISTS contracted_expected_weight NUMERIC,
    ADD COLUMN IF NOT EXISTS contracted_base_rate NUMERIC,
    ADD COLUMN IF NOT EXISTS contracted_fuel_surcharge NUMERIC,
    ADD COLUMN IF NOT EXISTS approved_accessorial_allowance NUMERIC,
    ADD COLUMN IF NOT EXISTS approved_accessorial_items JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS authorized_accessorial_codes TEXT[] NOT NULL DEFAULT '{}';

UPDATE public.purchase_orders
SET authorized_carrier = COALESCE(authorized_carrier, carrier_name),
    contracted_base_rate = COALESCE(contracted_base_rate, contracted_base_freight),
    contracted_fuel_surcharge = COALESCE(contracted_fuel_surcharge, agreed_fuel_surcharge),
    approved_accessorial_allowance = COALESCE(
        approved_accessorial_allowance,
        max_accessorial_allowance
    );

ALTER TABLE public.internal_dispatches
    ADD COLUMN IF NOT EXISTS authorized_carrier TEXT,
    ADD COLUMN IF NOT EXISTS contracted_expected_weight NUMERIC,
    ADD COLUMN IF NOT EXISTS approved_accessorial_allowance NUMERIC,
    ADD COLUMN IF NOT EXISTS approved_accessorial_items JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS authorized_accessorial_codes TEXT[] NOT NULL DEFAULT '{}';

UPDATE public.internal_dispatches
SET authorized_carrier = COALESCE(authorized_carrier, carrier_name),
    contracted_expected_weight = COALESCE(contracted_expected_weight, expected_weight),
    approved_accessorial_allowance = COALESCE(
        approved_accessorial_allowance,
        approved_accessorials
    );

ALTER TABLE public.warehouse_goods_receipts
    ADD COLUMN IF NOT EXISTS receipt_id TEXT,
    ADD COLUMN IF NOT EXISTS bol_number TEXT,
    ADD COLUMN IF NOT EXISTS received_timestamp TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS scale_ticket_id TEXT,
    ADD COLUMN IF NOT EXISTS receiving_dock_door TEXT,
    ADD COLUMN IF NOT EXISTS trailer_license_plate TEXT,
    ADD COLUMN IF NOT EXISTS gross_weight_lbs NUMERIC,
    ADD COLUMN IF NOT EXISTS tare_weight_lbs NUMERIC,
    ADD COLUMN IF NOT EXISTS certified_net_scale_weight NUMERIC,
    ADD COLUMN IF NOT EXISTS scale_certification_seal TEXT,
    ADD COLUMN IF NOT EXISTS osd_notes TEXT;

UPDATE public.warehouse_goods_receipts
SET received_timestamp = COALESCE(received_timestamp, delivery_timestamp),
    certified_net_scale_weight = COALESCE(certified_net_scale_weight, actual_scale_weight_lbs);

CREATE UNIQUE INDEX IF NOT EXISTS warehouse_goods_receipts_receipt_id_key
    ON public.warehouse_goods_receipts (receipt_id);

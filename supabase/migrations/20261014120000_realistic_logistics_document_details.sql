ALTER TABLE public.carrier_invoices
    ADD COLUMN IF NOT EXISTS bill_to_name TEXT,
    ADD COLUMN IF NOT EXISTS bill_to_address TEXT,
    ADD COLUMN IF NOT EXISTS remit_to_name TEXT,
    ADD COLUMN IF NOT EXISTS remit_to_address TEXT,
    ADD COLUMN IF NOT EXISTS payment_terms TEXT,
    ADD COLUMN IF NOT EXISTS payment_due_date DATE,
    ADD COLUMN IF NOT EXISTS pickup_date DATE,
    ADD COLUMN IF NOT EXISTS delivery_date DATE,
    ADD COLUMN IF NOT EXISTS freight_class TEXT,
    ADD COLUMN IF NOT EXISTS nmfc_code TEXT,
    ADD COLUMN IF NOT EXISTS rate_tariff_reference TEXT,
    ADD COLUMN IF NOT EXISTS rate_per_cwt NUMERIC,
    ADD COLUMN IF NOT EXISTS handling_unit_count INTEGER,
    ADD COLUMN IF NOT EXISTS handling_unit_type TEXT;

ALTER TABLE public.purchase_orders
    ADD COLUMN IF NOT EXISTS incoterms TEXT,
    ADD COLUMN IF NOT EXISTS freight_charge_terms TEXT,
    ADD COLUMN IF NOT EXISTS payment_terms TEXT,
    ADD COLUMN IF NOT EXISTS equipment_type_required TEXT,
    ADD COLUMN IF NOT EXISTS temperature_controlled BOOLEAN,
    ADD COLUMN IF NOT EXISTS liftgate_required BOOLEAN,
    ADD COLUMN IF NOT EXISTS scheduled_pickup_window TSTZRANGE,
    ADD COLUMN IF NOT EXISTS scheduled_delivery_window TSTZRANGE,
    ADD COLUMN IF NOT EXISTS buyer_contact_email TEXT;

ALTER TABLE public.warehouse_goods_receipts
    ADD COLUMN IF NOT EXISTS driver_name TEXT,
    ADD COLUMN IF NOT EXISTS driver_cdl_number TEXT,
    ADD COLUMN IF NOT EXISTS carrier_scac TEXT,
    ADD COLUMN IF NOT EXISTS tractor_unit_number TEXT,
    ADD COLUMN IF NOT EXISTS security_seal_number TEXT,
    ADD COLUMN IF NOT EXISTS security_seal_status TEXT,
    ADD COLUMN IF NOT EXISTS scale_operator_id TEXT,
    ADD COLUMN IF NOT EXISTS scale_calibration_date DATE,
    ADD COLUMN IF NOT EXISTS yard_check_in_timestamp TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS dock_unloaded_timestamp TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS yard_departure_timestamp TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS received_pallet_count INTEGER,
    ADD COLUMN IF NOT EXISTS packaging_condition TEXT;

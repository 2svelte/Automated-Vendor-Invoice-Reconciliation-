-- The demo carrier contact is the project owner's inbox; replace it with verified
-- carrier billing contacts before using this workflow for real carrier disputes.
ALTER TABLE public.carrier_invoices
    ADD COLUMN IF NOT EXISTS carrier_email TEXT NOT NULL
        DEFAULT 'nadiaravindra07@gmail.com',
    ADD COLUMN IF NOT EXISTS dispute_status TEXT NOT NULL DEFAULT 'NOT_QUEUED',
    ADD COLUMN IF NOT EXISTS dispute_from_email TEXT,
    ADD COLUMN IF NOT EXISTS dispute_to_email TEXT,
    ADD COLUMN IF NOT EXISTS dispute_cc_email TEXT,
    ADD COLUMN IF NOT EXISTS dispute_subject TEXT,
    ADD COLUMN IF NOT EXISTS dispute_memo_body TEXT,
    ADD COLUMN IF NOT EXISTS dispute_queued_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS email_dispatch_status TEXT NOT NULL DEFAULT 'NOT_QUEUED',
    ADD COLUMN IF NOT EXISTS dispatched_at TIMESTAMPTZ;

ALTER TABLE public.purchase_orders
    ADD COLUMN IF NOT EXISTS carrier_email TEXT NOT NULL
        DEFAULT 'nadiaravindra07@gmail.com';

ALTER TABLE public.warehouse_goods_receipts
    ADD COLUMN IF NOT EXISTS carrier_email TEXT NOT NULL
        DEFAULT 'nadiaravindra07@gmail.com';

UPDATE public.carrier_invoices
SET carrier_email = 'nadiaravindra07@gmail.com'
WHERE carrier_email IS NULL OR btrim(carrier_email) = '';

UPDATE public.purchase_orders
SET carrier_email = 'nadiaravindra07@gmail.com'
WHERE carrier_email IS NULL OR btrim(carrier_email) = '';

UPDATE public.warehouse_goods_receipts
SET carrier_email = 'nadiaravindra07@gmail.com'
WHERE carrier_email IS NULL OR btrim(carrier_email) = '';

ALTER TABLE public.carrier_invoices
    ADD CONSTRAINT carrier_invoices_dispute_status_check
        CHECK (dispute_status IN ('NOT_QUEUED', 'PENDING_DISPATCH', 'DISPATCHED', 'FAILED')),
    ADD CONSTRAINT carrier_invoices_email_dispatch_status_check
        CHECK (email_dispatch_status IN ('NOT_QUEUED', 'PENDING', 'SENT', 'FAILED'));

CREATE INDEX IF NOT EXISTS carrier_invoices_email_dispatch_status_idx
    ON public.carrier_invoices (email_dispatch_status)
    WHERE email_dispatch_status = 'PENDING';

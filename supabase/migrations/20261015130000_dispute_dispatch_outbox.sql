CREATE TABLE IF NOT EXISTS public.dispute_dispatches (
    dispatch_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id TEXT NOT NULL
        REFERENCES public.carrier_invoices (invoice_id),
    carrier_name TEXT NOT NULL,
    dispute_to_email TEXT NOT NULL,
    dispute_cc_email TEXT NOT NULL DEFAULT '',
    dispute_subject TEXT NOT NULL,
    dispute_memo_body TEXT NOT NULL,
    email_dispatch_status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (email_dispatch_status IN ('PENDING', 'SENT', 'FAILED')),
    queued_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    dispatched_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS dispute_dispatches_status_queued_at_idx
    ON public.dispute_dispatches (email_dispatch_status, queued_at);

CREATE UNIQUE INDEX IF NOT EXISTS dispute_dispatches_one_pending_per_invoice_idx
    ON public.dispute_dispatches (invoice_id)
    WHERE email_dispatch_status = 'PENDING';

CREATE INDEX IF NOT EXISTS dispute_dispatches_invoice_queued_at_idx
    ON public.dispute_dispatches (invoice_id, queued_at DESC);

INSERT INTO public.dispute_dispatches (
    invoice_id,
    carrier_name,
    dispute_to_email,
    dispute_cc_email,
    dispute_subject,
    dispute_memo_body,
    email_dispatch_status,
    queued_at,
    dispatched_at
)
SELECT
    invoice_id,
    carrier_name,
    dispute_to_email,
    COALESCE(dispute_cc_email, ''),
    dispute_subject,
    dispute_memo_body,
    CASE
        WHEN email_dispatch_status IN ('PENDING', 'SENT', 'FAILED')
            THEN email_dispatch_status
        WHEN dispute_status = 'DISPATCHED' THEN 'SENT'
        WHEN dispute_status = 'FAILED' THEN 'FAILED'
        ELSE 'PENDING'
    END,
    COALESCE(dispute_queued_at, NOW()),
    dispatched_at
FROM public.carrier_invoices
WHERE dispute_to_email IS NOT NULL
    AND dispute_subject IS NOT NULL
    AND dispute_memo_body IS NOT NULL
    AND (email_dispatch_status IN ('PENDING', 'SENT', 'FAILED')
        OR dispute_status IN ('PENDING_DISPATCH', 'DISPATCHED', 'FAILED'))
    AND NOT EXISTS (
        SELECT 1
        FROM public.dispute_dispatches AS existing
        WHERE existing.invoice_id = carrier_invoices.invoice_id
    );

DROP INDEX IF EXISTS public.carrier_invoices_email_dispatch_status_idx;

ALTER TABLE public.carrier_invoices
    DROP CONSTRAINT IF EXISTS carrier_invoices_dispute_status_check,
    DROP CONSTRAINT IF EXISTS carrier_invoices_email_dispatch_status_check,
    DROP COLUMN IF EXISTS dispute_status,
    DROP COLUMN IF EXISTS dispute_from_email,
    DROP COLUMN IF EXISTS dispute_to_email,
    DROP COLUMN IF EXISTS dispute_cc_email,
    DROP COLUMN IF EXISTS dispute_subject,
    DROP COLUMN IF EXISTS dispute_memo_body,
    DROP COLUMN IF EXISTS dispute_queued_at,
    DROP COLUMN IF EXISTS email_dispatch_status,
    DROP COLUMN IF EXISTS dispatched_at;

CREATE OR REPLACE FUNCTION public.queue_dispute_dispatch(
    p_invoice_id TEXT,
    p_carrier_name TEXT,
    p_dispute_to_email TEXT,
    p_dispute_cc_email TEXT,
    p_dispute_subject TEXT,
    p_dispute_memo_body TEXT
)
RETURNS UUID
LANGUAGE plpgsql
AS $$
DECLARE
    dispatch_key UUID;
    queued_time TIMESTAMPTZ := NOW();
BEGIN
    IF EXISTS (
        SELECT 1
        FROM public.dispute_dispatches
        WHERE invoice_id = p_invoice_id
            AND email_dispatch_status = 'PENDING'
    ) THEN
        RAISE EXCEPTION 'A dispute dispatch is already pending for invoice %.', p_invoice_id;
    END IF;

    INSERT INTO public.dispute_dispatches (
        invoice_id,
        carrier_name,
        dispute_to_email,
        dispute_cc_email,
        dispute_subject,
        dispute_memo_body,
        email_dispatch_status,
        queued_at
    )
    VALUES (
        p_invoice_id,
        p_carrier_name,
        p_dispute_to_email,
        COALESCE(p_dispute_cc_email, ''),
        p_dispute_subject,
        p_dispute_memo_body,
        'PENDING',
        queued_time
    )
    RETURNING dispatch_id INTO dispatch_key;

    UPDATE public.carrier_invoices
    SET audit_status = 'DISPUTED',
        audited_at = queued_time
    WHERE invoice_id = p_invoice_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Invoice % was not found.', p_invoice_id;
    END IF;

    RETURN dispatch_key;
END;
$$;

ALTER TABLE public.dispute_dispatches
    ADD COLUMN IF NOT EXISTS dispute_from_email TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS error_message TEXT;

DROP FUNCTION IF EXISTS public.queue_dispute_dispatch(
    TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT
);

CREATE FUNCTION public.queue_dispute_dispatch(
    p_invoice_id TEXT,
    p_carrier_name TEXT,
    p_dispute_to_email TEXT,
    p_dispute_cc_email TEXT,
    p_dispute_subject TEXT,
    p_dispute_memo_body TEXT,
    p_dispute_evidence_pdf_base64 TEXT DEFAULT NULL,
    p_dispute_evidence_filename TEXT DEFAULT NULL,
    p_dispute_from_email TEXT DEFAULT ''
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
        dispute_from_email,
        dispute_subject,
        dispute_memo_body,
        dispute_evidence_pdf_base64,
        dispute_evidence_filename,
        email_dispatch_status,
        queued_at
    )
    VALUES (
        p_invoice_id,
        p_carrier_name,
        p_dispute_to_email,
        COALESCE(p_dispute_cc_email, ''),
        COALESCE(p_dispute_from_email, ''),
        p_dispute_subject,
        p_dispute_memo_body,
        p_dispute_evidence_pdf_base64,
        p_dispute_evidence_filename,
        'PENDING',
        queued_time
    )
    RETURNING dispatch_id INTO dispatch_key;

    UPDATE public.carrier_invoices
    SET audit_status = 'DISPUTED',
        audit_note = 'Dispute email queued for Power Automate dispatch.',
        audited_at = queued_time
    WHERE invoice_id = p_invoice_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Invoice % was not found.', p_invoice_id;
    END IF;

    RETURN dispatch_key;
END;
$$;

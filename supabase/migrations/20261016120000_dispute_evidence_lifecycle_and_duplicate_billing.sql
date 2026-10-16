DROP INDEX IF EXISTS public.carrier_invoices_invoice_number_key;

CREATE INDEX IF NOT EXISTS carrier_invoices_invoice_number_idx
    ON public.carrier_invoices (invoice_number);

ALTER TABLE public.carrier_invoices
    ADD COLUMN IF NOT EXISTS dispute_date TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS rebuttal_deadline TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS source_document_hash TEXT;

ALTER TABLE public.invoice_audit_events
    ADD COLUMN IF NOT EXISTS invoice_id TEXT
        REFERENCES public.carrier_invoices (invoice_id);

DROP TRIGGER IF EXISTS invoice_audit_events_append_only
    ON public.invoice_audit_events;

UPDATE public.invoice_audit_events AS event
SET invoice_id = invoice.invoice_id
FROM public.carrier_invoices AS invoice
WHERE event.invoice_id IS NULL
    AND event.invoice_number = invoice.invoice_number;

CREATE TRIGGER invoice_audit_events_append_only
BEFORE UPDATE OR DELETE
ON public.invoice_audit_events
FOR EACH ROW
EXECUTE FUNCTION public.prevent_invoice_audit_event_mutation();

CREATE INDEX IF NOT EXISTS invoice_audit_events_invoice_id_created_at_idx
    ON public.invoice_audit_events (invoice_id, created_at DESC);

UPDATE public.carrier_invoices AS invoice
SET dispute_date = dispatch.dispatched_at,
    rebuttal_deadline = dispatch.dispatched_at + INTERVAL '30 days'
FROM (
    SELECT DISTINCT ON (invoice_id)
        invoice_id,
        dispatched_at
    FROM public.dispute_dispatches
    WHERE email_dispatch_status = 'SENT'
        AND dispatched_at IS NOT NULL
    ORDER BY invoice_id, dispatched_at DESC
) AS dispatch
WHERE invoice.invoice_id = dispatch.invoice_id
    AND invoice.audit_status = 'DISPUTED'
    AND invoice.dispute_date IS NULL;

ALTER TABLE public.dispute_dispatches
    ADD COLUMN IF NOT EXISTS dispute_evidence_pdf_base64 TEXT,
    ADD COLUMN IF NOT EXISTS dispute_evidence_filename TEXT;

CREATE OR REPLACE FUNCTION public.record_invoice_audit_event()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.audit_status IS DISTINCT FROM OLD.audit_status
        OR NEW.audit_note IS DISTINCT FROM OLD.audit_note
        OR NEW.audited_at IS DISTINCT FROM OLD.audited_at THEN
        INSERT INTO public.invoice_audit_events (
            invoice_id,
            invoice_number,
            previous_status,
            audit_status,
            audit_note,
            created_at
        )
        VALUES (
            NEW.invoice_id,
            NEW.invoice_number,
            OLD.audit_status,
            NEW.audit_status,
            NEW.audit_note,
            COALESCE(NEW.audited_at, NOW())
        );
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public.record_confirmed_dispute_dispatch()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    sent_time TIMESTAMPTZ;
BEGIN
    IF NEW.email_dispatch_status = 'SENT'
        AND OLD.email_dispatch_status IS DISTINCT FROM 'SENT' THEN
        sent_time := COALESCE(NEW.dispatched_at, NOW());
        UPDATE public.carrier_invoices
        SET audit_status = CASE
                WHEN audit_status IN (
                    'REBUTTAL_RECEIVED',
                    'CLOSED_RESOLVED',
                    'CLOSED_OVERRIDDEN'
                ) THEN audit_status
                ELSE 'DISPUTED'
            END,
            dispute_date = COALESCE(dispute_date, sent_time),
            rebuttal_deadline = COALESCE(
                rebuttal_deadline,
                COALESCE(dispute_date, sent_time) + INTERVAL '30 days'
            ),
            audit_note = CASE
                WHEN audit_status IN (
                    'REBUTTAL_RECEIVED',
                    'CLOSED_RESOLVED',
                    'CLOSED_OVERRIDDEN'
                ) THEN audit_note
                ELSE 'Dispute email delivered; awaiting carrier response.'
            END,
            audited_at = sent_time
        WHERE invoice_id = NEW.invoice_id;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS dispute_dispatch_confirmed ON public.dispute_dispatches;

CREATE TRIGGER dispute_dispatch_confirmed
AFTER UPDATE OF email_dispatch_status
ON public.dispute_dispatches
FOR EACH ROW
EXECUTE FUNCTION public.record_confirmed_dispute_dispatch();

DROP FUNCTION IF EXISTS public.queue_dispute_dispatch(
    TEXT, TEXT, TEXT, TEXT, TEXT, TEXT
);

CREATE FUNCTION public.queue_dispute_dispatch(
    p_invoice_id TEXT,
    p_carrier_name TEXT,
    p_dispute_to_email TEXT,
    p_dispute_cc_email TEXT,
    p_dispute_subject TEXT,
    p_dispute_memo_body TEXT,
    p_dispute_evidence_pdf_base64 TEXT DEFAULT NULL,
    p_dispute_evidence_filename TEXT DEFAULT NULL
)
RETURNS UUID
LANGUAGE plpgsql
AS $$
DECLARE
    dispatch_key UUID;
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
        dispute_evidence_pdf_base64,
        dispute_evidence_filename,
        email_dispatch_status
    )
    VALUES (
        p_invoice_id,
        p_carrier_name,
        p_dispute_to_email,
        COALESCE(p_dispute_cc_email, ''),
        p_dispute_subject,
        p_dispute_memo_body,
        p_dispute_evidence_pdf_base64,
        p_dispute_evidence_filename,
        'PENDING'
    )
    RETURNING dispatch_id INTO dispatch_key;

    RETURN dispatch_key;
END;
$$;

ALTER TABLE public.carrier_invoices
    ADD COLUMN IF NOT EXISTS audit_status TEXT,
    ADD COLUMN IF NOT EXISTS auditor_name TEXT,
    ADD COLUMN IF NOT EXISTS audit_note TEXT,
    ADD COLUMN IF NOT EXISTS audited_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS public.invoice_audit_events (
    event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    invoice_number TEXT NOT NULL,
    previous_status TEXT,
    audit_status TEXT NOT NULL,
    auditor_name TEXT NOT NULL,
    audit_note TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS invoice_audit_events_invoice_created_at_idx
    ON public.invoice_audit_events (invoice_number, created_at DESC);

CREATE OR REPLACE FUNCTION public.record_invoice_audit_event()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.audit_status IS DISTINCT FROM OLD.audit_status
        OR NEW.audit_note IS DISTINCT FROM OLD.audit_note
        OR NEW.audited_at IS DISTINCT FROM OLD.audited_at THEN
        INSERT INTO public.invoice_audit_events (
            invoice_number,
            previous_status,
            audit_status,
            auditor_name,
            audit_note,
            created_at
        )
        VALUES (
            NEW.invoice_number,
            OLD.audit_status,
            NEW.audit_status,
            NEW.auditor_name,
            NEW.audit_note,
            COALESCE(NEW.audited_at, NOW())
        );
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS carrier_invoice_audit_event ON public.carrier_invoices;

CREATE TRIGGER carrier_invoice_audit_event
AFTER UPDATE OF audit_status, auditor_name, audit_note, audited_at
ON public.carrier_invoices
FOR EACH ROW
EXECUTE FUNCTION public.record_invoice_audit_event();

CREATE OR REPLACE FUNCTION public.prevent_invoice_audit_event_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'Invoice audit events are append-only.';
END;
$$;

DROP TRIGGER IF EXISTS invoice_audit_events_append_only ON public.invoice_audit_events;

CREATE TRIGGER invoice_audit_events_append_only
BEFORE UPDATE OR DELETE
ON public.invoice_audit_events
FOR EACH ROW
EXECUTE FUNCTION public.prevent_invoice_audit_event_mutation();

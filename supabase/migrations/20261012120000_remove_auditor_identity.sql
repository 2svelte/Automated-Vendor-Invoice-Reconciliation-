DROP TRIGGER IF EXISTS carrier_invoice_audit_event ON public.carrier_invoices;

ALTER TABLE public.invoice_audit_events
    ALTER COLUMN auditor_name DROP NOT NULL;

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
            audit_note,
            created_at
        )
        VALUES (
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

CREATE TRIGGER carrier_invoice_audit_event
AFTER UPDATE OF audit_status, audit_note, audited_at
ON public.carrier_invoices
FOR EACH ROW
EXECUTE FUNCTION public.record_invoice_audit_event();

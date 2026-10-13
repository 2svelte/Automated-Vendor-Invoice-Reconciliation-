-- Fictional demo-only operational details for sample PO-1004 / PO-1007 records.
UPDATE public.purchase_orders
SET incoterms = 'FOB Destination (DEMO)',
    freight_charge_terms = 'Prepaid & Add (DEMO)',
    payment_terms = 'Net 30 (DEMO)',
    equipment_type_required = '53ft Dry Van (DEMO)',
    temperature_controlled = FALSE,
    liftgate_required = TRUE,
    scheduled_pickup_window = tstzrange(
        '2026-09-02 08:00:00-07'::timestamptz,
        '2026-09-02 12:00:00-07'::timestamptz,
        '[)'
    ),
    scheduled_delivery_window = tstzrange(
        '2026-09-04 08:00:00-07'::timestamptz,
        '2026-09-04 12:00:00-07'::timestamptz,
        '[)'
    ),
    buyer_contact_email = 'demo-procurement@example.com'
WHERE po_number = 'PO-1004';

UPDATE public.purchase_orders
SET incoterms = 'FOB Destination (DEMO)',
    freight_charge_terms = 'Prepaid & Add (DEMO)',
    payment_terms = 'Net 30 (DEMO)',
    equipment_type_required = '53ft Dry Van (DEMO)',
    temperature_controlled = FALSE,
    liftgate_required = TRUE,
    scheduled_pickup_window = tstzrange(
        '2026-09-05 08:00:00-06'::timestamptz,
        '2026-09-05 12:00:00-06'::timestamptz,
        '[)'
    ),
    scheduled_delivery_window = tstzrange(
        '2026-09-07 08:00:00-06'::timestamptz,
        '2026-09-07 12:00:00-06'::timestamptz,
        '[)'
    ),
    buyer_contact_email = 'demo-procurement@example.com'
WHERE po_number = 'PO-1007';

UPDATE public.warehouse_goods_receipts
SET driver_name = 'DEMO Driver 1004',
    driver_cdl_number = 'DEMO-CDL-1004',
    carrier_scac = 'NFRE',
    tractor_unit_number = 'DEMO-TRACTOR-04',
    security_seal_number = 'DEMO-SEAL-1004',
    security_seal_status = 'Intact',
    scale_operator_id = 'DEMO-OP-01',
    scale_calibration_date = '2026-01-15',
    yard_check_in_timestamp = '2026-09-04 08:10:00-07',
    dock_unloaded_timestamp = '2026-09-04 10:05:00-07',
    yard_departure_timestamp = '2026-09-04 10:35:00-07',
    received_pallet_count = 4,
    packaging_condition = 'Good (DEMO)'
WHERE po_number = 'PO-1004';

UPDATE public.warehouse_goods_receipts
SET driver_name = 'DEMO Driver 1007',
    driver_cdl_number = 'DEMO-CDL-1007',
    carrier_scac = 'NFRE',
    tractor_unit_number = 'DEMO-TRACTOR-07',
    security_seal_number = 'DEMO-SEAL-1007',
    security_seal_status = 'Intact',
    scale_operator_id = 'DEMO-OP-02',
    scale_calibration_date = '2026-01-15',
    yard_check_in_timestamp = '2026-09-07 08:10:00-06',
    dock_unloaded_timestamp = '2026-09-07 10:05:00-06',
    yard_departure_timestamp = '2026-09-07 10:35:00-06',
    received_pallet_count = 10,
    packaging_condition = 'Good (DEMO)'
WHERE po_number = 'PO-1007';

BEGIN;

-- P0: Merge pixel_id variant delivery settings rows into canonical client_id rows.
-- enutri-4sph (pixel_id) → enutri (canonical): copy 4 recipients, preserve enabled flags from canonical.
UPDATE core_report_delivery_settings
SET
    recipients = (
        SELECT recipients
        FROM core_report_delivery_settings
        WHERE client_id = 'enutri-4sph'
    ),
    updated_at = now()
WHERE client_id = 'enutri';

-- dipua-qe5p (pixel_id) → dipua (canonical): copy 4 recipients, preserve enabled flags from canonical.
UPDATE core_report_delivery_settings
SET
    recipients = (
        SELECT recipients
        FROM core_report_delivery_settings
        WHERE client_id = 'dipua-qe5p'
    ),
    updated_at = now()
WHERE client_id = 'dipua';

-- Remove the now-merged pixel_id variant rows.
DELETE FROM core_report_delivery_settings WHERE client_id IN ('enutri-4sph', 'dipua-qe5p');

COMMIT;

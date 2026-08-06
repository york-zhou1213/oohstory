-- An existing real cover must not enter AI image editing merely because it
-- contains source branding.  Local TXT80/TXT020 covers go through the three-
-- source lookup queue; other real-source covers remain available as-is.

UPDATE library_clean_cover_jobs AS j
JOIN books AS b ON b.id=j.catalog_id
SET j.status=CASE
      WHEN b.library_id='local' AND b.source_id REGEXP '^[0-9]+$'
        THEN 'source_lookup_pending'
      ELSE 'source_cover_retained'
    END,
    j.attempts=0,
    j.last_error=CASE
      WHEN b.library_id='local' AND b.source_id REGEXP '^[0-9]+$'
        THEN '等待三站精确匹配，禁止直接调用 AI'
      ELSE '真实源站封面已保留；没有确认无资源，禁止调用 AI'
    END,
    j.lease_owner=NULL,
    j.lease_token=NULL,
    j.lease_expires_at=NULL
WHERE j.original_filename IS NOT NULL
  AND j.status IN ('pending','manual_pending','processing','failed');

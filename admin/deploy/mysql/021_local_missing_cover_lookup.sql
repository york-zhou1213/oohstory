-- Local TXT80/TXT020 books with no original image must also exhaust the three
-- real sources before title-based AI generation is allowed.

UPDATE library_clean_cover_jobs AS j
JOIN books AS b ON b.id=j.catalog_id
SET j.status='source_lookup_missing_cover',
    j.attempts=0,
    j.last_error='本地无封面；先检索三站，确认无资源后才允许 AI 文生图',
    j.lease_owner=NULL,
    j.lease_token=NULL,
    j.lease_expires_at=NULL
WHERE b.library_id='local'
  AND b.source_id REGEXP '^[0-9]+$'
  AND j.original_filename IS NULL
  AND j.status='generate_pending';

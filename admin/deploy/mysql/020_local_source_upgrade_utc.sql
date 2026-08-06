-- Claims compare against UTC_TIMESTAMP, so historical/default availability
-- values must use the same clock on hosts whose SYSTEM timezone is not UTC.

UPDATE local_source_upgrade_jobs
SET available_at=UTC_TIMESTAMP(6)
WHERE status IN ('pending','failed')
  AND available_at>UTC_TIMESTAMP(6);

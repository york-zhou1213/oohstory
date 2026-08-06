-- Replace watermarked local TXT80/TXT020 covers from exact matches on the
-- three owner-authorized online sources.  Keep the catalog source and the
-- cover origin separate when only the cover is upgraded.

ALTER TABLE library_covers
    ADD COLUMN cover_source_id VARCHAR(255) NULL AFTER detail_url,
    ADD COLUMN cover_detail_url VARCHAR(2048) NULL AFTER cover_source_id;

CREATE TABLE IF NOT EXISTS local_source_upgrade_jobs (
    catalog_id BIGINT UNSIGNED NOT NULL,
    original_source_id VARCHAR(255) NOT NULL,
    status VARCHAR(24) NOT NULL DEFAULT 'pending',
    attempts SMALLINT UNSIGNED NOT NULL DEFAULT 0,
    max_attempts SMALLINT UNSIGNED NOT NULL DEFAULT 5,
    matched_source_name VARCHAR(32) NULL,
    matched_source_id VARCHAR(255) NULL,
    matched_detail_url VARCHAR(2048) NULL,
    local_latest_chapter VARCHAR(255) NULL,
    remote_latest_chapter VARCHAR(255) NULL,
    local_chapter_number INT UNSIGNED NULL,
    remote_chapter_number INT UNSIGNED NULL,
    cover_replaced TINYINT(1) NOT NULL DEFAULT 0,
    body_replaced TINYINT(1) NOT NULL DEFAULT 0,
    ai_fallback_queued TINYINT(1) NOT NULL DEFAULT 0,
    last_error TEXT NULL,
    lease_owner VARCHAR(255) NULL,
    lease_token CHAR(36) NULL,
    lease_expires_at DATETIME(6) NULL,
    available_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    completed_at DATETIME(6) NULL,
    PRIMARY KEY (catalog_id),
    KEY idx_local_source_upgrade_claim (
        status, available_at, attempts, catalog_id
    ),
    CONSTRAINT fk_local_source_upgrade_book
        FOREIGN KEY (catalog_id) REFERENCES books(id) ON DELETE CASCADE
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci
  ROW_FORMAT=DYNAMIC;

-- Existing TXT80/TXT020 watermark jobs must pass through the three-source
-- lookup before the AI worker is allowed to claim them.  Confirmed
-- title-generation fallbacks have original_filename=NULL and are deliberately
-- left as generate_pending.
UPDATE library_clean_cover_jobs AS j
JOIN books AS b ON b.id=j.catalog_id
SET j.status='source_lookup_pending',
    j.attempts=0,
    j.last_error='等待爱下、新笔趣阁、书宝三站精确匹配后再决定是否需要 AI',
    j.lease_owner=NULL,
    j.lease_token=NULL,
    j.lease_expires_at=NULL
WHERE b.library_id='local'
  AND b.source_id REGEXP '^[0-9]+$'
  AND j.original_filename IS NOT NULL
  AND j.status IN ('pending','manual_pending','processing','failed');

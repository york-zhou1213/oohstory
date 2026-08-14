CREATE TABLE IF NOT EXISTS audiobook_cast_scan_jobs (
    catalog_id BIGINT UNSIGNED NOT NULL,
    book_public_id VARCHAR(22) NOT NULL,
    content_revision BINARY(32) NOT NULL,
    engine_version VARCHAR(40) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    total_chapters INT UNSIGNED NOT NULL DEFAULT 0,
    processed_chapters INT UNSIGNED NOT NULL DEFAULT 0,
    last_chapter_id INT UNSIGNED NULL,
    lease_token CHAR(32) NULL,
    lease_until DATETIME(6) NULL,
    error_count INT UNSIGNED NOT NULL DEFAULT 0,
    last_error VARCHAR(1000) NULL,
    next_attempt_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    started_at DATETIME(6) NULL,
    completed_at DATETIME(6) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (catalog_id),
    KEY idx_audiobook_cast_scan_claim
        (status, next_attempt_at, lease_until, updated_at),
    CONSTRAINT fk_audiobook_cast_scan_book FOREIGN KEY (catalog_id)
        REFERENCES books(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS audiobook_character_profiles (
    catalog_id BIGINT UNSIGNED NOT NULL,
    character_key BINARY(32) NOT NULL,
    canonical_name VARCHAR(120) NOT NULL,
    scan_revision BINARY(32) NOT NULL,
    role_type VARCHAR(16) NOT NULL DEFAULT 'unclassified',
    mention_count INT UNSIGNED NOT NULL DEFAULT 0,
    dialogue_count INT UNSIGNED NOT NULL DEFAULT 0,
    chapter_count INT UNSIGNED NOT NULL DEFAULT 0,
    first_chapter_id INT UNSIGNED NOT NULL,
    last_chapter_id INT UNSIGNED NOT NULL,
    voice_locked TINYINT(1) NOT NULL DEFAULT 1,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (catalog_id, character_key),
    KEY idx_audiobook_character_profile_role
        (catalog_id, scan_revision, role_type, chapter_count),
    CONSTRAINT fk_audiobook_character_profile_voice
        FOREIGN KEY (catalog_id, character_key)
        REFERENCES audiobook_character_voices(catalog_id, character_key)
        ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS audiobook_cast_ai_review_jobs (
    catalog_id BIGINT UNSIGNED NOT NULL,
    book_public_id VARCHAR(22) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    content_revision BINARY(32) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    lease_token CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL,
    lease_until DATETIME(6) NULL,
    attempt_count INT UNSIGNED NOT NULL DEFAULT 0,
    last_error VARCHAR(1000) NULL,
    next_attempt_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    completed_at DATETIME(6) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (catalog_id),
    KEY idx_audiobook_cast_ai_claim
        (status,next_attempt_at,lease_until,updated_at),
    CONSTRAINT fk_audiobook_cast_ai_job_book FOREIGN KEY (catalog_id)
        REFERENCES books(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS audiobook_cast_ai_reviews (
    catalog_id BIGINT UNSIGNED NOT NULL,
    character_key BINARY(32) NOT NULL,
    scan_revision BINARY(32) NOT NULL,
    gender VARCHAR(16) NOT NULL DEFAULT 'unknown',
    role_type VARCHAR(16) NOT NULL DEFAULT 'cameo',
    confidence DECIMAL(5,4) NOT NULL DEFAULT 0,
    reason VARCHAR(500) NOT NULL,
    model_key VARCHAR(160) NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (catalog_id,character_key,scan_revision),
    KEY idx_audiobook_cast_ai_reviews_revision (catalog_id,scan_revision),
    CONSTRAINT fk_audiobook_cast_ai_review_profile
        FOREIGN KEY (catalog_id,character_key)
        REFERENCES audiobook_character_profiles(catalog_id,character_key)
        ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

INSERT INTO audiobook_cast_ai_review_jobs
    (catalog_id,book_public_id,content_revision,status,next_attempt_at)
SELECT j.catalog_id,j.book_public_id,j.content_revision,'pending',UTC_TIMESTAMP(6)
FROM audiobook_cast_scan_jobs j
WHERE j.status='complete' AND EXISTS (
    SELECT 1 FROM audiobook_character_profiles p
    INNER JOIN audiobook_character_voices v
        ON v.catalog_id=p.catalog_id AND v.character_key=p.character_key
    WHERE p.catalog_id=j.catalog_id AND p.scan_revision=j.content_revision
      AND (v.gender='unknown' OR v.gender_confidence<0.7
           OR (p.role_type='protagonist' AND p.canonical_name<>'主人公'))
)
ON DUPLICATE KEY UPDATE
    book_public_id=VALUES(book_public_id),
    status=IF(audiobook_cast_ai_review_jobs.content_revision=VALUES(content_revision)
              AND audiobook_cast_ai_review_jobs.status='complete','complete','pending'),
    content_revision=VALUES(content_revision),
    next_attempt_at=UTC_TIMESTAMP(6);

GRANT SELECT, INSERT, UPDATE ON oohstory_library.audiobook_cast_scan_jobs
    TO 'oohstory_audiobook_role'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON oohstory_library.audiobook_character_profiles
    TO 'oohstory_audiobook_role'@'%';
GRANT DELETE ON oohstory_library.audiobook_character_voices
    TO 'oohstory_audiobook_role'@'%';
GRANT SELECT, INSERT, UPDATE ON oohstory_library.audiobook_cast_ai_review_jobs
    TO 'oohstory_audiobook_role'@'%';
GRANT SELECT, INSERT, UPDATE ON oohstory_library.audiobook_cast_ai_reviews
    TO 'oohstory_audiobook_role'@'%';

ALTER TABLE books
    ADD COLUMN approx_word_count BIGINT UNSIGNED NOT NULL DEFAULT 0
        AFTER bytes,
    ADD COLUMN approx_chapter_count INT UNSIGNED NOT NULL DEFAULT 0
        AFTER approx_word_count,
    ADD COLUMN effective_word_count BIGINT UNSIGNED
        GENERATED ALWAYS AS (
            CASE
                WHEN approx_word_count > 0 THEN approx_word_count
                ELSE bytes DIV 3
            END
        ) STORED AFTER approx_chapter_count,
    ADD COLUMN serialization_code TINYINT UNSIGNED
        GENERATED ALWAYS AS (
            CASE
                WHEN book_status IN (
                    '已完结', '完结', 'finished', 'completed'
                ) THEN 1
                ELSE 0
            END
        ) STORED AFTER book_status,
    ADD COLUMN sha256_bin BINARY(32)
        GENERATED ALWAYS AS (
            CASE
                WHEN sha256 REGEXP '^[0-9A-Fa-f]{64}$'
                THEN UNHEX(sha256)
                ELSE NULL
            END
        ) STORED AFTER sha256,
    DROP INDEX idx_books_sha256,
    ADD INDEX idx_books_sha256_bin (sha256_bin),
    ADD INDEX idx_books_public_recent (
        library_id, body_available, is_active, id DESC
    ),
    ADD INDEX idx_books_public_category_recent (
        library_id, body_available, is_active, category, id DESC
    ),
    ADD INDEX idx_books_public_words (
        library_id, body_available, is_active,
        effective_word_count DESC, id DESC
    ),
    ADD INDEX idx_books_public_serialization (
        library_id, body_available, is_active, serialization_code, id DESC
    ),
    ADD INDEX idx_books_title_lookup (
        title(191), body_available, bytes DESC, id DESC
    );

ALTER TABLE object_assets
    ADD COLUMN sha256_bin BINARY(32)
        GENERATED ALWAYS AS (
            CASE
                WHEN sha256 REGEXP '^[0-9A-Fa-f]{64}$'
                THEN UNHEX(sha256)
                ELSE NULL
            END
        ) STORED AFTER sha256,
    DROP INDEX idx_object_assets_sha256,
    ADD INDEX idx_object_assets_sha256_bin (sha256_bin);

ALTER TABLE download_jobs
    DROP INDEX idx_download_jobs_lease,
    ADD INDEX idx_download_jobs_dispatch (
        source_name, status, priority, attempts, id, available_at
    ),
    ADD INDEX idx_download_jobs_expired_lease (
        status, lease_expires_at
    ),
    ADD INDEX idx_download_jobs_stale_queue (
        status, updated_at
    );

CREATE TABLE IF NOT EXISTS book_metadata (
    catalog_id BIGINT UNSIGNED NOT NULL,
    source_mtime_ns BIGINT UNSIGNED NOT NULL DEFAULT 0,
    summary TEXT NULL,
    genre_tags JSON NULL,
    tone_tags JSON NULL,
    keyword_counts JSON NULL,
    primary_tone_tags JSON NULL,
    secondary_tone_tags JSON NULL,
    tone_confidence DECIMAL(6,5) NOT NULL DEFAULT 0,
    tone_source VARCHAR(32) NOT NULL DEFAULT 'local',
    tone_evidence JSON NULL,
    tone_review_status VARCHAR(32) NOT NULL DEFAULT 'pending',
    tone_review_model VARCHAR(255) NOT NULL DEFAULT '',
    tone_reviewed_at DATETIME(6) NULL,
    section_count INT UNSIGNED NOT NULL DEFAULT 0,
    reader_index_status VARCHAR(32) NOT NULL DEFAULT '',
    reader_schema_version SMALLINT UNSIGNED NOT NULL DEFAULT 0,
    reader_indexed_at DATETIME(6) NULL,
    indexed_at DATETIME(6) NULL,
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (catalog_id),
    KEY idx_book_metadata_review (
        tone_review_status, tone_reviewed_at, catalog_id
    ),
    CONSTRAINT fk_book_metadata_book
        FOREIGN KEY (catalog_id) REFERENCES books(id) ON DELETE CASCADE
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC;

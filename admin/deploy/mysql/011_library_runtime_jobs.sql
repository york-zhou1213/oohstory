-- Durable runtime state for operational electronic-library workers.
-- Redis may accelerate delivery/coordination, but every job and result below
-- remains recoverable from MySQL after FLUSHDB or a Redis restart.

CREATE TABLE IF NOT EXISTS library_postprocess_jobs (
    catalog_id BIGINT UNSIGNED NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    attempts INT UNSIGNED NOT NULL DEFAULT 0,
    max_attempts INT UNSIGNED NOT NULL DEFAULT 4,
    available_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    lease_owner VARCHAR(255) NULL,
    lease_token CHAR(36) NULL,
    lease_expires_at DATETIME(6) NULL,
    last_error VARCHAR(2000) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    completed_at DATETIME(6) NULL,
    PRIMARY KEY (catalog_id),
    KEY idx_library_postprocess_claim
        (status, available_at, attempts, catalog_id),
    KEY idx_library_postprocess_lease (status, lease_expires_at),
    CONSTRAINT fk_library_postprocess_book
        FOREIGN KEY (catalog_id) REFERENCES books(id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS library_covers (
    catalog_id BIGINT UNSIGNED NOT NULL,
    source_id VARCHAR(255) NOT NULL,
    title VARCHAR(512) NOT NULL DEFAULT '',
    author VARCHAR(255) NOT NULL DEFAULT '',
    detail_url TEXT NOT NULL,
    cover_url TEXT NULL,
    filename VARCHAR(1024) NULL,
    sha256 CHAR(64) NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    attempts INT UNSIGNED NOT NULL DEFAULT 0,
    last_error VARCHAR(2000) NULL,
    lease_owner VARCHAR(255) NULL,
    lease_token CHAR(36) NULL,
    lease_expires_at DATETIME(6) NULL,
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (catalog_id),
    KEY idx_library_covers_claim (status, attempts, catalog_id),
    KEY idx_library_covers_filename (filename(191)),
    CONSTRAINT fk_library_covers_book
        FOREIGN KEY (catalog_id) REFERENCES books(id) ON DELETE CASCADE
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC;

CREATE TABLE IF NOT EXISTS library_fanqie_cover_jobs (
    catalog_id BIGINT UNSIGNED NOT NULL,
    catalog_source_id VARCHAR(255) NOT NULL,
    title VARCHAR(512) NOT NULL DEFAULT '',
    author VARCHAR(255) NOT NULL DEFAULT '',
    book_id VARCHAR(64) NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    attempts INT UNSIGNED NOT NULL DEFAULT 0,
    last_error VARCHAR(2000) NULL,
    resolved_by VARCHAR(255) NULL,
    lease_owner VARCHAR(255) NULL,
    lease_token CHAR(36) NULL,
    lease_expires_at DATETIME(6) NULL,
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (catalog_id),
    KEY idx_library_fanqie_cover_claim (status, attempts, catalog_id),
    CONSTRAINT fk_library_fanqie_cover_book
        FOREIGN KEY (catalog_id) REFERENCES books(id) ON DELETE CASCADE
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC;

CREATE TABLE IF NOT EXISTS library_clean_cover_jobs (
    catalog_id BIGINT UNSIGNED NOT NULL,
    source_id VARCHAR(255) NOT NULL,
    title VARCHAR(512) NOT NULL DEFAULT '',
    author VARCHAR(255) NOT NULL DEFAULT '',
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    original_filename VARCHAR(1024) NULL,
    replacement_url TEXT NULL,
    replacement_filename VARCHAR(1024) NULL,
    verification_source VARCHAR(255) NULL,
    last_error VARCHAR(2000) NULL,
    attempts INT UNSIGNED NOT NULL DEFAULT 0,
    ai_session_id VARCHAR(255) NULL,
    source_width INT UNSIGNED NULL,
    source_height INT UNSIGNED NULL,
    generated_width INT UNSIGNED NULL,
    generated_height INT UNSIGNED NULL,
    original_deleted_at DATETIME(6) NULL,
    lease_owner VARCHAR(255) NULL,
    lease_token CHAR(36) NULL,
    lease_expires_at DATETIME(6) NULL,
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (catalog_id),
    KEY idx_library_clean_cover_claim (status, attempts, catalog_id),
    CONSTRAINT fk_library_clean_cover_book
        FOREIGN KEY (catalog_id) REFERENCES books(id) ON DELETE CASCADE
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC;

ALTER TABLE book_metadata
    ADD COLUMN reader_source_path TEXT NULL AFTER source_mtime_ns,
    ADD COLUMN reader_source_bytes BIGINT UNSIGNED NOT NULL DEFAULT 0
        AFTER reader_source_path,
    ADD COLUMN word_count BIGINT UNSIGNED NOT NULL DEFAULT 0
        AFTER reader_source_bytes,
    ADD COLUMN chapter_count INT UNSIGNED NOT NULL DEFAULT 0
        AFTER word_count,
    ADD KEY idx_book_metadata_reader_fingerprint
        (reader_source_bytes, source_mtime_ns);

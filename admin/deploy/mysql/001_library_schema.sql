-- Webnovel Writer catalog schema for MySQL 8.0.
-- MySQL is the durable source of truth. Redis only accelerates queues/cache;
-- book bodies and covers remain external objects on NAS/object storage.

SET NAMES utf8mb4 COLLATE utf8mb4_0900_ai_ci;
SET time_zone = '+00:00';

CREATE TABLE IF NOT EXISTS schema_migrations (
    version VARCHAR(64) NOT NULL PRIMARY KEY,
    applied_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    checksum CHAR(64) NOT NULL,
    description VARCHAR(255) NOT NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS books (
    id BIGINT UNSIGNED NOT NULL,
    source_id VARCHAR(255) NULL,
    detail_url TEXT NOT NULL,
    detail_url_hash BINARY(32)
        GENERATED ALWAYS AS (UNHEX(SHA2(detail_url, 256))) STORED,
    title VARCHAR(512) NOT NULL DEFAULT '',
    author VARCHAR(255) NOT NULL DEFAULT '',
    category VARCHAR(100) NOT NULL DEFAULT '未分类',
    expected_size VARCHAR(100) NULL,
    download_page_url TEXT NULL,
    file_url TEXT NULL,
    legacy_output_path TEXT NULL,
    body_object_key VARCHAR(1024) NULL,
    cover_object_key VARCHAR(1024) NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'discovered',
    attempts INT UNSIGNED NOT NULL DEFAULT 0,
    bytes BIGINT UNSIGNED NOT NULL DEFAULT 0,
    sha256 CHAR(64) NULL,
    last_error VARCHAR(2000) NULL,
    discovered_at DATETIME(6) NULL,
    updated_at DATETIME(6) NULL,
    book_status VARCHAR(32) NOT NULL DEFAULT '已完结',
    identity_key VARCHAR(768) NULL,
    identity_hash BINARY(32)
        GENERATED ALWAYS AS (
            UNHEX(SHA2(COALESCE(identity_key, ''), 256))
        ) STORED,
    title_key VARCHAR(768) NULL,
    library_id VARCHAR(32) NOT NULL DEFAULT 'local',
    body_available TINYINT(1)
        GENERATED ALWAYS AS (
            status = 'done'
            AND (
                NULLIF(TRIM(COALESCE(body_object_key, '')), '') IS NOT NULL
                OR NULLIF(TRIM(COALESCE(legacy_output_path, '')), '') IS NOT NULL
            )
        ) STORED,
    row_version BIGINT UNSIGNED NOT NULL DEFAULT 1,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    mysql_updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uq_books_detail_url_hash (detail_url_hash),
    UNIQUE KEY uq_books_source_id (source_id),
    KEY idx_books_library_body_category_id
        (library_id, body_available, category, id DESC),
    KEY idx_books_library_status_id
        (library_id, status, id DESC),
    KEY idx_books_download_queue (status, attempts, id),
    KEY idx_books_identity_hash (identity_hash),
    KEY idx_books_sha256 (sha256),
    FULLTEXT KEY ftx_books_title_author (title, author) WITH PARSER ngram,
    CONSTRAINT chk_books_library
        CHECK (library_id IN ('local', 'fanqie'))
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC;

CREATE TABLE IF NOT EXISTS library_membership_events (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    catalog_id BIGINT UNSIGNED NOT NULL,
    previous_library_id VARCHAR(32) NULL,
    target_library_id VARCHAR(32) NOT NULL,
    reason VARCHAR(100) NOT NULL DEFAULT 'manual',
    moved_at DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    KEY idx_membership_events_catalog (catalog_id, moved_at DESC),
    CONSTRAINT fk_membership_events_book
        FOREIGN KEY (catalog_id) REFERENCES books(id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS catalog_facets (
    library_id VARCHAR(32) NOT NULL,
    body_available TINYINT(1) NOT NULL,
    category VARCHAR(100) NOT NULL,
    book_count BIGINT UNSIGNED NOT NULL,
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (library_id, body_available, category)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS download_jobs (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    catalog_id BIGINT UNSIGNED NOT NULL,
    source_id VARCHAR(255) NOT NULL,
    source_name VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    priority SMALLINT NOT NULL DEFAULT 100,
    attempts INT UNSIGNED NOT NULL DEFAULT 0,
    max_attempts INT UNSIGNED NOT NULL DEFAULT 8,
    available_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    lease_owner VARCHAR(255) NULL,
    lease_token CHAR(36) NULL,
    lease_expires_at DATETIME(6) NULL,
    last_error VARCHAR(2000) NULL,
    payload JSON NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    completed_at DATETIME(6) NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_download_jobs_catalog (catalog_id),
    KEY idx_download_jobs_claim
        (status, available_at, priority, attempts, id),
    KEY idx_download_jobs_lease (lease_expires_at, status),
    KEY idx_download_jobs_source (source_name, status, id),
    CONSTRAINT fk_download_jobs_book
        FOREIGN KEY (catalog_id) REFERENCES books(id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS object_assets (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    catalog_id BIGINT UNSIGNED NOT NULL,
    asset_type VARCHAR(32) NOT NULL,
    object_key VARCHAR(1024) NOT NULL,
    object_key_hash BINARY(32)
        GENERATED ALWAYS AS (UNHEX(SHA2(object_key, 256))) STORED,
    storage_backend VARCHAR(32) NOT NULL DEFAULT 'nas',
    content_type VARCHAR(255) NULL,
    bytes BIGINT UNSIGNED NOT NULL DEFAULT 0,
    sha256 CHAR(64) NULL,
    etag VARCHAR(255) NULL,
    state VARCHAR(32) NOT NULL DEFAULT 'available',
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uq_object_assets_book_type (catalog_id, asset_type),
    UNIQUE KEY uq_object_assets_object_key_hash (object_key_hash),
    KEY idx_object_assets_sha256 (sha256),
    CONSTRAINT fk_object_assets_book
        FOREIGN KEY (catalog_id) REFERENCES books(id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS crawl_pages (
    source_name VARCHAR(64) NOT NULL,
    section_key VARCHAR(255) NOT NULL DEFAULT '',
    page_number INT UNSIGNED NOT NULL,
    url TEXT NOT NULL,
    url_hash BINARY(32)
        GENERATED ALWAYS AS (UNHEX(SHA2(url, 256))) STORED,
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    attempts INT UNSIGNED NOT NULL DEFAULT 0,
    book_count INT UNSIGNED NULL,
    last_error VARCHAR(2000) NULL,
    updated_at DATETIME(6) NULL,
    PRIMARY KEY (source_name, section_key, page_number),
    UNIQUE KEY uq_crawl_pages_url_hash (url_hash),
    KEY idx_crawl_pages_queue (source_name, status, attempts, page_number)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS crawl_state (
    source_name VARCHAR(64) NOT NULL,
    state_key VARCHAR(255) NOT NULL,
    state_value JSON NOT NULL,
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (source_name, state_key)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS duplicate_books (
    duplicate_book_id BIGINT UNSIGNED NOT NULL,
    duplicate_source_id VARCHAR(255) NULL,
    kept_book_id BIGINT UNSIGNED NOT NULL,
    kept_source_id VARCHAR(255) NULL,
    title VARCHAR(512) NULL,
    author VARCHAR(255) NULL,
    removed_object_key VARCHAR(1024) NULL,
    removed_path TEXT NULL,
    removed_bytes BIGINT UNSIGNED NOT NULL DEFAULT 0,
    removed_sha256 CHAR(64) NULL,
    duplicate_metrics JSON NULL,
    kept_metrics JSON NULL,
    reason VARCHAR(255) NOT NULL,
    removed_at DATETIME(6) NOT NULL,
    PRIMARY KEY (duplicate_book_id),
    KEY idx_duplicate_books_kept (kept_book_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS library_sync_runs (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    started_at DATETIME(6) NOT NULL,
    finished_at DATETIME(6) NULL,
    status VARCHAR(32) NOT NULL,
    summary JSON NULL,
    PRIMARY KEY (id),
    KEY idx_library_sync_runs_started (started_at DESC)
) ENGINE=InnoDB;

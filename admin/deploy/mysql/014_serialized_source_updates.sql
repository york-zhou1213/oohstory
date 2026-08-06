-- Daily recent-update checkpoints for owner-authorized online sources.
-- The catalog/body remains canonical in books; this table only records the
-- remote revision observed and whether that revision reached the local body.

CREATE TABLE IF NOT EXISTS authorized_source_updates (
    source_name VARCHAR(64) NOT NULL,
    source_id VARCHAR(255) NOT NULL,
    catalog_id BIGINT UNSIGNED NULL,
    title VARCHAR(512) NOT NULL DEFAULT '',
    author VARCHAR(255) NOT NULL DEFAULT '',
    detail_url TEXT NOT NULL,
    remote_revision VARCHAR(1024) NOT NULL DEFAULT '',
    applied_revision VARCHAR(1024) NOT NULL DEFAULT '',
    remote_latest_chapter VARCHAR(1024) NOT NULL DEFAULT '',
    local_latest_chapter VARCHAR(1024) NOT NULL DEFAULT '',
    remote_updated_at VARCHAR(64) NOT NULL DEFAULT '',
    state VARCHAR(32) NOT NULL DEFAULT 'observed',
    last_error VARCHAR(2000) NULL,
    first_seen_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    last_seen_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    applied_at DATETIME(6) NULL,
    PRIMARY KEY (source_name, source_id),
    KEY idx_authorized_source_updates_catalog (catalog_id),
    KEY idx_authorized_source_updates_state
        (state, source_name, last_seen_at),
    CONSTRAINT fk_authorized_source_updates_book
        FOREIGN KEY (catalog_id) REFERENCES books(id) ON DELETE SET NULL
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC;

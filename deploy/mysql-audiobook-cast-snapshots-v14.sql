CREATE TABLE IF NOT EXISTS audiobook_cast_snapshots (
    catalog_id BIGINT UNSIGNED NOT NULL,
    content_revision BINARY(32) NOT NULL,
    engine_version VARCHAR(64) NOT NULL,
    revision BIGINT UNSIGNED NOT NULL,
    cast_json JSON NOT NULL,
    published_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (catalog_id),
    KEY idx_audiobook_cast_snapshot_revision (catalog_id,revision),
    CONSTRAINT fk_audiobook_cast_snapshot_book FOREIGN KEY (catalog_id)
        REFERENCES books(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

GRANT SELECT, INSERT, UPDATE ON oohstory_library.audiobook_cast_snapshots
    TO 'oohstory_audiobook_role'@'%';

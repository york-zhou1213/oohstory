CREATE TABLE IF NOT EXISTS catalog_sections (
    source_name VARCHAR(64) NOT NULL,
    section_key VARCHAR(255) NOT NULL,
    path TEXT NOT NULL,
    path_hash BINARY(32)
        GENERATED ALWAYS AS (UNHEX(SHA2(path, 256))) STORED,
    label VARCHAR(255) NULL,
    total_pages INT UNSIGNED NOT NULL DEFAULT 0,
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    attempts INT UNSIGNED NOT NULL DEFAULT 0,
    last_error VARCHAR(2000) NULL,
    updated_at DATETIME(6) NULL,
    PRIMARY KEY (source_name, section_key),
    UNIQUE KEY uq_catalog_sections_path_hash (path_hash),
    KEY idx_catalog_sections_status (source_name, status, attempts)
) ENGINE=InnoDB;

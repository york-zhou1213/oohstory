CREATE TABLE IF NOT EXISTS audiobook_cast_revisions (
    catalog_id BIGINT UNSIGNED NOT NULL,
    revision BIGINT UNSIGNED NOT NULL DEFAULT 0,
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (catalog_id),
    CONSTRAINT fk_audiobook_cast_revision_book FOREIGN KEY (catalog_id)
        REFERENCES books(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

SET @has_cast_revision = (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema=DATABASE()
      AND table_name='audiobook_chapter_manifests'
      AND column_name='cast_revision'
);
SET @ddl = IF(
    @has_cast_revision=0,
    'ALTER TABLE audiobook_chapter_manifests ADD COLUMN cast_revision BIGINT UNSIGNED NOT NULL DEFAULT 0 AFTER engine_version',
    'SELECT 1'
);
PREPARE audiobook_runtime_stmt FROM @ddl;
EXECUTE audiobook_runtime_stmt;
DEALLOCATE PREPARE audiobook_runtime_stmt;

SET @primary_has_cast_revision = (
    SELECT COUNT(*) FROM information_schema.statistics
    WHERE table_schema=DATABASE()
      AND table_name='audiobook_chapter_manifests'
      AND index_name='PRIMARY'
      AND column_name='cast_revision'
);
SET @ddl = IF(
    @primary_has_cast_revision=0,
    'ALTER TABLE audiobook_chapter_manifests DROP PRIMARY KEY, ADD PRIMARY KEY (catalog_id,chapter_id,content_hash,settings_hash,engine_version,cast_revision)',
    'SELECT 1'
);
PREPARE audiobook_runtime_stmt FROM @ddl;
EXECUTE audiobook_runtime_stmt;
DEALLOCATE PREPARE audiobook_runtime_stmt;

CREATE TABLE IF NOT EXISTS audiobook_tts_concurrency_guard (
    guard_id TINYINT UNSIGNED NOT NULL,
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (guard_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
INSERT IGNORE INTO audiobook_tts_concurrency_guard (guard_id) VALUES (1);

CREATE TABLE IF NOT EXISTS audiobook_tts_leases (
    lease_token CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    owner_hash BINARY(32) NOT NULL,
    ip_hash BINARY(32) NOT NULL,
    expires_at DATETIME(6) NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (lease_token),
    KEY idx_audiobook_tts_owner (owner_hash,expires_at),
    KEY idx_audiobook_tts_ip (ip_hash,expires_at),
    KEY idx_audiobook_tts_expiry (expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS audiobook_progress (
    owner_hash BINARY(32) NOT NULL,
    catalog_id BIGINT UNSIGNED NOT NULL,
    book_public_id VARCHAR(22) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    chapter_id INT UNSIGNED NOT NULL,
    paragraph_index INT UNSIGNED NOT NULL DEFAULT 0,
    item_index INT UNSIGNED NOT NULL DEFAULT 0,
    audio_offset_ms INT UNSIGNED NOT NULL DEFAULT 0,
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (owner_hash,catalog_id),
    KEY idx_audiobook_progress_updated (updated_at),
    CONSTRAINT fk_audiobook_progress_book FOREIGN KEY (catalog_id)
        REFERENCES books(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

SET @has_duration_ms = (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema=DATABASE()
      AND table_name='audiobook_audio_jobs'
      AND column_name='duration_ms'
);
SET @ddl = IF(
    @has_duration_ms=0,
    'ALTER TABLE audiobook_audio_jobs ADD COLUMN duration_ms INT UNSIGNED NULL AFTER byte_count',
    'SELECT 1'
);
PREPARE audiobook_runtime_stmt FROM @ddl;
EXECUTE audiobook_runtime_stmt;
DEALLOCATE PREPARE audiobook_runtime_stmt;

GRANT SELECT, INSERT, UPDATE ON oohstory_library.audiobook_cast_revisions
    TO 'oohstory_audiobook_role'@'%';
GRANT SELECT, INSERT, UPDATE ON oohstory_library.audiobook_tts_concurrency_guard
    TO 'oohstory_audiobook_role'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON oohstory_library.audiobook_tts_leases
    TO 'oohstory_audiobook_role'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON oohstory_library.audiobook_progress
    TO 'oohstory_audiobook_role'@'%';
GRANT DELETE ON oohstory_library.audiobook_chapter_manifests
    TO 'oohstory_audiobook_role'@'%';

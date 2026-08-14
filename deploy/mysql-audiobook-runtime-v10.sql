CREATE TABLE IF NOT EXISTS audiobook_device_progress (
    owner_hash BINARY(32) NOT NULL,
    catalog_id BIGINT UNSIGNED NOT NULL,
    device_hash BINARY(32) NOT NULL,
    book_public_id VARCHAR(22) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    chapter_id INT UNSIGNED NOT NULL,
    paragraph_index INT UNSIGNED NOT NULL DEFAULT 0,
    item_index INT UNSIGNED NOT NULL DEFAULT 0,
    audio_offset_ms INT UNSIGNED NOT NULL DEFAULT 0,
    manifest_hash BINARY(32) NOT NULL,
    settings_hash BINARY(32) NOT NULL,
    cast_revision BIGINT UNSIGNED NOT NULL DEFAULT 0,
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (owner_hash,catalog_id,device_hash),
    KEY idx_audiobook_device_progress_latest (owner_hash,catalog_id,updated_at),
    KEY idx_audiobook_device_progress_updated (updated_at),
    CONSTRAINT fk_audiobook_device_progress_book FOREIGN KEY (catalog_id)
        REFERENCES books(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

SET @has_voice_lock = (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema=DATABASE()
      AND table_name='audiobook_character_voices'
      AND column_name='voice_locked'
);
SET @ddl = IF(
    @has_voice_lock=0,
    'ALTER TABLE audiobook_character_voices ADD COLUMN voice_locked TINYINT(1) NOT NULL DEFAULT 1 AFTER voice_key',
    'SELECT 1'
);
PREPARE audiobook_v10_stmt FROM @ddl;
EXECUTE audiobook_v10_stmt;
DEALLOCATE PREPARE audiobook_v10_stmt;

-- Preserve the old account-wide checkpoint as a compatibility seed.  A real
-- client device creates its own row on the first v10 progress write.
INSERT IGNORE INTO audiobook_device_progress
    (owner_hash,catalog_id,device_hash,book_public_id,chapter_id,
     paragraph_index,item_index,audio_offset_ms,manifest_hash,settings_hash,
     cast_revision,updated_at)
SELECT p.owner_hash,p.catalog_id,p.owner_hash,p.book_public_id,p.chapter_id,
       p.paragraph_index,p.item_index,0,m.manifest_hash,m.settings_hash,
       m.cast_revision,p.updated_at
FROM audiobook_progress p
INNER JOIN audiobook_chapter_manifests m
    ON m.catalog_id=p.catalog_id AND m.chapter_id=p.chapter_id
LEFT JOIN audiobook_chapter_manifests newer
    ON newer.catalog_id=m.catalog_id AND newer.chapter_id=m.chapter_id
   AND (newer.created_at>m.created_at
        OR (newer.created_at=m.created_at AND newer.manifest_hash>m.manifest_hash))
WHERE newer.manifest_hash IS NULL;

GRANT SELECT, INSERT, UPDATE, DELETE
    ON oohstory_library.audiobook_device_progress
    TO 'oohstory_audiobook_role'@'%';

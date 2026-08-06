-- Per-book completion facts for resumable derived indexes.
-- The former global tone_rule_version flag could only describe a completed
-- whole-library run.  If that run stopped halfway, every retry restarted from
-- book zero.  These columns make each committed book its own durable checkpoint.

SET @ddl = IF(
    EXISTS(
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = 'book_metadata'
          AND column_name = 'source_sha256'
    ),
    'SELECT 1',
    "ALTER TABLE book_metadata ADD COLUMN source_sha256 CHAR(64) NOT NULL DEFAULT '' AFTER source_mtime_ns"
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl = IF(
    EXISTS(
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = 'book_metadata'
          AND column_name = 'tone_rule_version'
    ),
    'SELECT 1',
    "ALTER TABLE book_metadata ADD COLUMN tone_rule_version VARCHAR(64) NOT NULL DEFAULT '' AFTER source_sha256"
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl = IF(
    EXISTS(
        SELECT 1 FROM information_schema.statistics
        WHERE table_schema = DATABASE()
          AND table_name = 'book_metadata'
          AND index_name = 'idx_book_metadata_tone_checkpoint'
    ),
    'SELECT 1',
    'ALTER TABLE book_metadata ADD KEY idx_book_metadata_tone_checkpoint (tone_rule_version, catalog_id)'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl = IF(
    EXISTS(
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = 'plot_index_meta'
          AND column_name = 'source_sha256'
    ),
    'SELECT 1',
    "ALTER TABLE plot_index_meta ADD COLUMN source_sha256 CHAR(64) NOT NULL DEFAULT '' AFTER source_mtime_ns"
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl = IF(
    EXISTS(
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = 'plot_index_meta'
          AND column_name = 'plot_rule_version'
    ),
    'SELECT 1',
    "ALTER TABLE plot_index_meta ADD COLUMN plot_rule_version VARCHAR(64) NOT NULL DEFAULT '' AFTER source_sha256"
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl = IF(
    EXISTS(
        SELECT 1 FROM information_schema.statistics
        WHERE table_schema = DATABASE()
          AND table_name = 'plot_index_meta'
          AND index_name = 'idx_plot_index_checkpoint'
    ),
    'SELECT 1',
    'ALTER TABLE plot_index_meta ADD KEY idx_plot_index_checkpoint (plot_rule_version, catalog_id)'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Existing plot rows were all produced by the same extractor version.  Preserve
-- that completed work and attach the catalog content hash without reading files.
UPDATE plot_index_meta p
JOIN books b ON b.id = p.catalog_id
SET p.source_sha256 = COALESCE(b.sha256, ''),
    p.plot_rule_version = '2026-07-28.1'
WHERE p.plot_rule_version = '';

-- A completed historical tone run has a trustworthy global version.  Promote
-- it to per-book checkpoints.  Interrupted runs intentionally have no global
-- version and are reconciled by the deployment/resume step instead.
UPDATE book_metadata m
JOIN books b ON b.id = m.catalog_id
JOIN crawl_state s
  ON s.source_name = 'library-metadata'
 AND s.state_key = 'tone_rule_version'
SET m.source_sha256 = COALESCE(b.sha256, ''),
    m.tone_rule_version = JSON_UNQUOTE(s.state_value)
WHERE m.tone_rule_version = ''
  AND JSON_UNQUOTE(s.state_value) <> '';

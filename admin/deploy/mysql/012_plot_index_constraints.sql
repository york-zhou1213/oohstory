-- Repair installations where plot tables were created by the application
-- before migration 010 ran. CREATE TABLE IF NOT EXISTS does not add indexes
-- or constraints to an existing table, so every operation below is
-- deliberately idempotent.

SET @ddl = IF(
    EXISTS(
        SELECT 1
        FROM information_schema.statistics
        WHERE table_schema = DATABASE()
          AND table_name = 'plot_segments'
          AND index_name = 'idx_plot_segments_catalog'
    ),
    'SELECT 1',
    'ALTER TABLE plot_segments ADD KEY idx_plot_segments_catalog (catalog_id, id)'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl = IF(
    EXISTS(
        SELECT 1
        FROM information_schema.statistics
        WHERE table_schema = DATABASE()
          AND table_name = 'plot_segments'
          AND index_name = 'idx_plot_segments_source'
    ),
    'SELECT 1',
    'ALTER TABLE plot_segments ADD KEY idx_plot_segments_source (source_id, id)'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl = IF(
    EXISTS(
        SELECT 1
        FROM information_schema.statistics
        WHERE table_schema = DATABASE()
          AND table_name = 'plot_segments'
          AND index_name = 'ftx_plot_segments_search'
    ),
    'SELECT 1',
    'ALTER TABLE plot_segments ADD FULLTEXT KEY ftx_plot_segments_search (location, motif_text, content) WITH PARSER ngram'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl = IF(
    EXISTS(
        SELECT 1
        FROM information_schema.table_constraints
        WHERE constraint_schema = DATABASE()
          AND table_name = 'plot_segments'
          AND constraint_name = 'fk_plot_segments_book'
          AND constraint_type = 'FOREIGN KEY'
    ),
    'SELECT 1',
    'ALTER TABLE plot_segments ADD CONSTRAINT fk_plot_segments_book FOREIGN KEY (catalog_id) REFERENCES books (id) ON DELETE CASCADE, ALGORITHM=INPLACE, LOCK=NONE'
);
SET @old_foreign_key_checks = @@SESSION.foreign_key_checks;
SET SESSION foreign_key_checks = 0;
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
SET SESSION foreign_key_checks = @old_foreign_key_checks;

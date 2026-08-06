CREATE TABLE IF NOT EXISTS plot_index_meta (
    catalog_id BIGINT UNSIGNED NOT NULL,
    source_id VARCHAR(255) NOT NULL,
    source_bytes BIGINT UNSIGNED NOT NULL DEFAULT 0,
    source_mtime_ns BIGINT UNSIGNED NOT NULL DEFAULT 0,
    segment_count INT UNSIGNED NOT NULL DEFAULT 0,
    indexed_at DATETIME(6) NULL,
    updated_at TIMESTAMP(6) NOT NULL
        DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (catalog_id),
    UNIQUE KEY uq_plot_index_meta_source (source_id),
    KEY idx_plot_index_meta_recent (indexed_at, catalog_id),
    CONSTRAINT fk_plot_index_meta_book
        FOREIGN KEY (catalog_id) REFERENCES books (id)
        ON DELETE CASCADE
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci
  ROW_FORMAT=DYNAMIC;

CREATE TABLE IF NOT EXISTS plot_segments (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    catalog_id BIGINT UNSIGNED NOT NULL,
    source_id VARCHAR(255) NOT NULL,
    location VARCHAR(512) NOT NULL DEFAULT '',
    motif_tags JSON NULL,
    motif_text VARCHAR(1024) NOT NULL DEFAULT '',
    content MEDIUMTEXT NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    KEY idx_plot_segments_catalog (catalog_id, id),
    KEY idx_plot_segments_source (source_id, id),
    FULLTEXT KEY ftx_plot_segments_search (
        location,
        motif_text,
        content
    ) WITH PARSER ngram,
    CONSTRAINT fk_plot_segments_book
        FOREIGN KEY (catalog_id) REFERENCES books (id)
        ON DELETE CASCADE
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci
  ROW_FORMAT=DYNAMIC;

ALTER TABLE book_metadata
    ADD KEY idx_book_metadata_recent (indexed_at, catalog_id),
    ADD FULLTEXT KEY ftx_book_metadata_summary (summary) WITH PARSER ngram;

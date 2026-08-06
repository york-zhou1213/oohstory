-- Anonymous, per-book public read and download counters.
-- The application must record a visitor action and increment its aggregate
-- counter in the same transaction. The composite visitor primary key is the
-- concurrency boundary that prevents duplicate visitors for one book.

CREATE TABLE IF NOT EXISTS book_public_metrics (
    catalog_id BIGINT UNSIGNED NOT NULL,
    read_count BIGINT UNSIGNED NOT NULL DEFAULT 0,
    download_count BIGINT UNSIGNED NOT NULL DEFAULT 0,
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (catalog_id),
    CONSTRAINT fk_book_public_metrics_book
        FOREIGN KEY (catalog_id) REFERENCES books(id) ON DELETE CASCADE
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci
  ROW_FORMAT=DYNAMIC;

CREATE TABLE IF NOT EXISTS book_public_metric_visitors (
    catalog_id BIGINT UNSIGNED NOT NULL,
    visitor_hash BINARY(32) NOT NULL,
    first_read_at DATETIME(6) NULL,
    first_download_at DATETIME(6) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (catalog_id, visitor_hash),
    CONSTRAINT fk_book_public_metric_visitors_book
        FOREIGN KEY (catalog_id) REFERENCES books(id) ON DELETE CASCADE
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci
  ROW_FORMAT=DYNAMIC;

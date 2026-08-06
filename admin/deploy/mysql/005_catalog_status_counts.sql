CREATE TABLE IF NOT EXISTS catalog_status_counts (
    library_id VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL,
    book_count BIGINT UNSIGNED NOT NULL,
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (library_id, status)
) ENGINE=InnoDB;

INSERT INTO catalog_status_counts (library_id, status, book_count)
SELECT library_id, status, COUNT(*)
FROM books
GROUP BY library_id, status
ON DUPLICATE KEY UPDATE book_count=VALUES(book_count);

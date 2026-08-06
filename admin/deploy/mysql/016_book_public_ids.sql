-- Stable, non-sequential public identifiers for OOH Story book URLs.
-- Internal catalog primary keys never need to be exposed by the public reader.

CREATE TABLE IF NOT EXISTS book_public_ids (
    catalog_id BIGINT UNSIGNED NOT NULL,
    public_id BINARY(16) NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (catalog_id),
    UNIQUE KEY uq_book_public_ids_public_id (public_id),
    CONSTRAINT fk_book_public_ids_book
        FOREIGN KEY (catalog_id) REFERENCES books(id) ON DELETE CASCADE
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci
  ROW_FORMAT=DYNAMIC;

-- Keep future catalog inserts covered without changing every authorized source
-- importer. ROW binlogging records the generated value rather than replaying
-- RANDOM_BYTES() on a replica.
DROP TRIGGER IF EXISTS trg_books_public_id_after_insert;
DELIMITER $$
CREATE TRIGGER trg_books_public_id_after_insert
AFTER INSERT ON books
FOR EACH ROW
BEGIN
    INSERT INTO book_public_ids (catalog_id, public_id)
    VALUES (NEW.id, RANDOM_BYTES(16));
END$$
DELIMITER ;

-- Backfill all existing books. A second pass closes the tiny deployment race
-- between table creation and trigger installation and retries any theoretical
-- random collision skipped by INSERT IGNORE.
INSERT IGNORE INTO book_public_ids (catalog_id, public_id)
SELECT b.id, RANDOM_BYTES(16)
FROM books b
LEFT JOIN book_public_ids p ON p.catalog_id = b.id
WHERE p.catalog_id IS NULL;

INSERT IGNORE INTO book_public_ids (catalog_id, public_id)
SELECT b.id, RANDOM_BYTES(16)
FROM books b
LEFT JOIN book_public_ids p ON p.catalog_id = b.id
WHERE p.catalog_id IS NULL;

-- Reversible operator-controlled publication state for OOHStory Reader.
-- Catalog/download lifecycle remains represented by status/is_active;
-- is_published controls only public Reader visibility.

ALTER TABLE books
    ADD COLUMN is_published TINYINT(1) NOT NULL DEFAULT 1,
    ALGORITHM=COPY;

CREATE TABLE book_publication_events (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    catalog_id BIGINT UNSIGNED NOT NULL,
    previous_published TINYINT(1) NOT NULL,
    target_published TINYINT(1) NOT NULL,
    reason VARCHAR(240) NOT NULL,
    changed_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    KEY idx_publication_events_catalog (catalog_id, id DESC),
    CONSTRAINT fk_publication_events_book
        FOREIGN KEY (catalog_id) REFERENCES books(id) ON DELETE CASCADE
) ENGINE=InnoDB;

DROP TRIGGER IF EXISTS trg_books_public_counts_ai;
DROP TRIGGER IF EXISTS trg_books_public_counts_au;
DROP TRIGGER IF EXISTS trg_books_public_counts_ad;

DELIMITER //

CREATE TRIGGER trg_books_public_counts_ai
AFTER INSERT ON books
FOR EACH ROW
BEGIN
    IF NEW.is_active=1 AND NEW.body_available=1 AND NEW.is_published=1 THEN
        INSERT INTO public_catalog_facets (
            category, serialization_code, word_bucket, book_count
        )
        VALUES (
            COALESCE(NULLIF(NEW.category, ''), '未分类'),
            NEW.serialization_code,
            NEW.word_bucket,
            1
        )
        ON DUPLICATE KEY UPDATE book_count=book_count+1;
    END IF;
END//

CREATE TRIGGER trg_books_public_counts_au
AFTER UPDATE ON books
FOR EACH ROW
BEGIN
    IF OLD.is_active <> NEW.is_active
       OR OLD.body_available <> NEW.body_available
       OR OLD.is_published <> NEW.is_published
       OR COALESCE(NULLIF(OLD.category, ''), '未分类')
          <> COALESCE(NULLIF(NEW.category, ''), '未分类')
       OR OLD.serialization_code <> NEW.serialization_code
       OR OLD.word_bucket <> NEW.word_bucket
    THEN
        IF OLD.is_active=1 AND OLD.body_available=1 AND OLD.is_published=1 THEN
            UPDATE public_catalog_facets
            SET book_count=IF(book_count > 0, book_count-1, 0)
            WHERE category=COALESCE(NULLIF(OLD.category, ''), '未分类')
              AND serialization_code=OLD.serialization_code
              AND word_bucket=OLD.word_bucket;
            DELETE FROM public_catalog_facets
            WHERE category=COALESCE(NULLIF(OLD.category, ''), '未分类')
              AND serialization_code=OLD.serialization_code
              AND word_bucket=OLD.word_bucket
              AND book_count=0;
        END IF;

        IF NEW.is_active=1 AND NEW.body_available=1 AND NEW.is_published=1 THEN
            INSERT INTO public_catalog_facets (
                category, serialization_code, word_bucket, book_count
            )
            VALUES (
                COALESCE(NULLIF(NEW.category, ''), '未分类'),
                NEW.serialization_code,
                NEW.word_bucket,
                1
            )
            ON DUPLICATE KEY UPDATE book_count=book_count+1;
        END IF;
    END IF;
END//

CREATE TRIGGER trg_books_public_counts_ad
AFTER DELETE ON books
FOR EACH ROW
BEGIN
    IF OLD.is_active=1 AND OLD.body_available=1 AND OLD.is_published=1 THEN
        UPDATE public_catalog_facets
        SET book_count=IF(book_count > 0, book_count-1, 0)
        WHERE category=COALESCE(NULLIF(OLD.category, ''), '未分类')
          AND serialization_code=OLD.serialization_code
          AND word_bucket=OLD.word_bucket;
        DELETE FROM public_catalog_facets
        WHERE category=COALESCE(NULLIF(OLD.category, ''), '未分类')
          AND serialization_code=OLD.serialization_code
          AND word_bucket=OLD.word_bucket
          AND book_count=0;
    END IF;
END//

DELIMITER ;

DELETE FROM public_catalog_facets;
INSERT INTO public_catalog_facets (
    category, serialization_code, word_bucket, book_count
)
SELECT
    COALESCE(NULLIF(category, ''), '未分类'),
    serialization_code,
    word_bucket,
    COUNT(*)
FROM books
WHERE is_active=1 AND body_available=1 AND is_published=1
GROUP BY
    COALESCE(NULLIF(category, ''), '未分类'),
    serialization_code,
    word_bucket;

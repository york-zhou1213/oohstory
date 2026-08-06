ALTER TABLE books
    ADD COLUMN word_bucket TINYINT UNSIGNED
        GENERATED ALWAYS AS (
            CASE
                WHEN effective_word_count < 100000 THEN 0
                WHEN effective_word_count < 200000 THEN 1
                WHEN effective_word_count < 300000 THEN 2
                WHEN effective_word_count < 500000 THEN 3
                WHEN effective_word_count < 1000000 THEN 4
                WHEN effective_word_count < 2000000 THEN 5
                ELSE 6
            END
        ) STORED AFTER effective_word_count;

CREATE TABLE public_catalog_facets (
    category VARCHAR(100) NOT NULL,
    serialization_code TINYINT UNSIGNED NOT NULL,
    word_bucket TINYINT UNSIGNED NOT NULL,
    book_count BIGINT UNSIGNED NOT NULL,
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (category, serialization_code, word_bucket),
    KEY idx_public_catalog_filter (
        serialization_code, word_bucket, category
    )
) ENGINE=InnoDB;

INSERT INTO public_catalog_facets (
    category, serialization_code, word_bucket, book_count
)
SELECT
    COALESCE(NULLIF(category, ''), '未分类'),
    serialization_code,
    word_bucket,
    COUNT(*)
FROM books
WHERE is_active=1 AND body_available=1
GROUP BY
    COALESCE(NULLIF(category, ''), '未分类'),
    serialization_code,
    word_bucket;

DELIMITER //

CREATE TRIGGER trg_books_public_counts_ai
AFTER INSERT ON books
FOR EACH ROW
BEGIN
    IF NEW.is_active=1 AND NEW.body_available=1 THEN
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
       OR COALESCE(NULLIF(OLD.category, ''), '未分类')
          <> COALESCE(NULLIF(NEW.category, ''), '未分类')
       OR OLD.serialization_code <> NEW.serialization_code
       OR OLD.word_bucket <> NEW.word_bucket
    THEN
        IF OLD.is_active=1 AND OLD.body_available=1 THEN
            UPDATE public_catalog_facets
            SET book_count=IF(book_count > 0, book_count-1, 0)
            WHERE category=COALESCE(
                    NULLIF(OLD.category, ''), '未分类'
                  )
              AND serialization_code=OLD.serialization_code
              AND word_bucket=OLD.word_bucket;
            DELETE FROM public_catalog_facets
            WHERE category=COALESCE(
                    NULLIF(OLD.category, ''), '未分类'
                  )
              AND serialization_code=OLD.serialization_code
              AND word_bucket=OLD.word_bucket
              AND book_count=0;
        END IF;

        IF NEW.is_active=1 AND NEW.body_available=1 THEN
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
    IF OLD.is_active=1 AND OLD.body_available=1 THEN
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

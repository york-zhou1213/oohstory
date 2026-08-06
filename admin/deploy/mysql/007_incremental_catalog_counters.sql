DROP TRIGGER IF EXISTS trg_books_catalog_counts_ai;
DROP TRIGGER IF EXISTS trg_books_catalog_counts_au;
DROP TRIGGER IF EXISTS trg_books_catalog_counts_ad;

DELIMITER //

CREATE TRIGGER trg_books_catalog_counts_ai
AFTER INSERT ON books
FOR EACH ROW
BEGIN
    INSERT INTO catalog_status_counts (library_id, status, book_count)
    VALUES (NEW.library_id, NEW.status, 1)
    ON DUPLICATE KEY UPDATE book_count=book_count+1;

    IF NEW.is_active = 1 THEN
        INSERT INTO catalog_facets (
            library_id, body_available, category, book_count
        )
        VALUES (
            NEW.library_id,
            NEW.body_available,
            COALESCE(NULLIF(NEW.category, ''), '未分类'),
            1
        )
        ON DUPLICATE KEY UPDATE book_count=book_count+1;
    END IF;
END//

CREATE TRIGGER trg_books_catalog_counts_au
AFTER UPDATE ON books
FOR EACH ROW
BEGIN
    IF OLD.library_id <> NEW.library_id OR OLD.status <> NEW.status THEN
        UPDATE catalog_status_counts
        SET book_count=IF(book_count > 0, book_count-1, 0)
        WHERE library_id=OLD.library_id AND status=OLD.status;
        DELETE FROM catalog_status_counts
        WHERE library_id=OLD.library_id
          AND status=OLD.status
          AND book_count=0;

        INSERT INTO catalog_status_counts (library_id, status, book_count)
        VALUES (NEW.library_id, NEW.status, 1)
        ON DUPLICATE KEY UPDATE book_count=book_count+1;
    END IF;

    IF OLD.library_id <> NEW.library_id
       OR OLD.body_available <> NEW.body_available
       OR OLD.is_active <> NEW.is_active
       OR COALESCE(NULLIF(OLD.category, ''), '未分类')
          <> COALESCE(NULLIF(NEW.category, ''), '未分类')
    THEN
        IF OLD.is_active = 1 THEN
            UPDATE catalog_facets
            SET book_count=IF(book_count > 0, book_count-1, 0)
            WHERE library_id=OLD.library_id
              AND body_available=OLD.body_available
              AND category=COALESCE(
                  NULLIF(OLD.category, ''), '未分类'
              );
            DELETE FROM catalog_facets
            WHERE library_id=OLD.library_id
              AND body_available=OLD.body_available
              AND category=COALESCE(
                  NULLIF(OLD.category, ''), '未分类'
              )
              AND book_count=0;
        END IF;

        IF NEW.is_active = 1 THEN
            INSERT INTO catalog_facets (
                library_id, body_available, category, book_count
            )
            VALUES (
                NEW.library_id,
                NEW.body_available,
                COALESCE(NULLIF(NEW.category, ''), '未分类'),
                1
            )
            ON DUPLICATE KEY UPDATE book_count=book_count+1;
        END IF;
    END IF;
END//

CREATE TRIGGER trg_books_catalog_counts_ad
AFTER DELETE ON books
FOR EACH ROW
BEGIN
    UPDATE catalog_status_counts
    SET book_count=IF(book_count > 0, book_count-1, 0)
    WHERE library_id=OLD.library_id AND status=OLD.status;
    DELETE FROM catalog_status_counts
    WHERE library_id=OLD.library_id
      AND status=OLD.status
      AND book_count=0;

    IF OLD.is_active = 1 THEN
        UPDATE catalog_facets
        SET book_count=IF(book_count > 0, book_count-1, 0)
        WHERE library_id=OLD.library_id
          AND body_available=OLD.body_available
          AND category=COALESCE(NULLIF(OLD.category, ''), '未分类');
        DELETE FROM catalog_facets
        WHERE library_id=OLD.library_id
          AND body_available=OLD.body_available
          AND category=COALESCE(NULLIF(OLD.category, ''), '未分类')
          AND book_count=0;
    END IF;
END//

DELIMITER ;

DELETE FROM catalog_facets;
INSERT INTO catalog_facets (
    library_id, body_available, category, book_count
)
SELECT
    library_id,
    body_available,
    COALESCE(NULLIF(category, ''), '未分类'),
    COUNT(*)
FROM books
WHERE is_active=1
GROUP BY
    library_id,
    body_available,
    COALESCE(NULLIF(category, ''), '未分类');

DELETE FROM catalog_status_counts;
INSERT INTO catalog_status_counts (library_id, status, book_count)
SELECT library_id, status, COUNT(*)
FROM books
GROUP BY library_id, status;

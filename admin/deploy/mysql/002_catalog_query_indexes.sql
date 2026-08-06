ALTER TABLE books
    ADD COLUMN is_active TINYINT(1)
        GENERATED ALWAYS AS (status <> 'duplicate') STORED
        AFTER status,
    ADD INDEX idx_books_active_id (is_active, id DESC),
    ADD INDEX idx_books_active_library_id (
        is_active, library_id, id DESC
    );

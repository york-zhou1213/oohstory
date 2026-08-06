ALTER TABLE books
    ADD INDEX idx_books_readable_recent (
        body_available, is_active, id DESC
    ),
    ADD INDEX idx_books_readable_category_recent (
        body_available, is_active, category, id DESC
    ),
    ADD INDEX idx_books_readable_words (
        body_available, is_active, effective_word_count DESC, id DESC
    ),
    ADD INDEX idx_books_readable_serialization (
        body_available, is_active, serialization_code, id DESC
    ),
    ADD INDEX idx_books_readable_title (
        body_available, is_active, title, id DESC
    );

ANALYZE TABLE books, download_jobs, object_assets, book_metadata;

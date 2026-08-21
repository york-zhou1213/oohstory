-- Serve each public category's longest Reader-ready books without scanning and
-- sorting the full half-million-row catalog on every cold homepage request.
ALTER TABLE books
    ADD INDEX idx_books_reader_ready_category_words (
        body_available,
        is_active,
        is_published,
        category,
        effective_word_count DESC,
        id DESC,
        bytes
    ),
    ALGORITHM=INPLACE,
    LOCK=NONE;

ANALYZE TABLE books;

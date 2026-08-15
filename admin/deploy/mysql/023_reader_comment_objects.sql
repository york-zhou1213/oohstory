-- Reader comment metadata. Comment bodies remain content-addressed objects
-- under the configured comment object root and are never stored in MySQL.

CREATE TABLE reader_comments (
    id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    catalog_id BIGINT UNSIGNED NOT NULL,
    book_public_id VARCHAR(22) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    scope VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    user_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    chapter_id INT UNSIGNED NULL,
    paragraph_index INT UNSIGNED NULL,
    paragraph_key VARCHAR(80) NOT NULL DEFAULT '',
    object_key VARCHAR(500) NOT NULL,
    object_sha256 BINARY(32) NOT NULL,
    object_bytes INT UNSIGNED NOT NULL,
    status VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin
        NOT NULL DEFAULT 'visible',
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uq_reader_comment_object (object_key),
    KEY idx_reader_comments_book
        (book_public_id, scope, status, created_at, id),
    KEY idx_reader_comments_paragraph
        (book_public_id, chapter_id, paragraph_key, status, created_at, id),
    KEY idx_reader_comments_user (user_id, created_at),
    CONSTRAINT fk_reader_comments_book
        FOREIGN KEY (catalog_id) REFERENCES books(id) ON DELETE CASCADE,
    CONSTRAINT chk_reader_comments_scope
        CHECK (scope IN ('book', 'paragraph')),
    CONSTRAINT chk_reader_comments_status
        CHECK (status IN ('visible', 'hidden')),
    CONSTRAINT chk_reader_comments_location CHECK (
        (
            scope='book'
            AND chapter_id IS NULL
            AND paragraph_index IS NULL
            AND paragraph_key=''
        )
        OR
        (
            scope='paragraph'
            AND chapter_id > 0
            AND paragraph_index IS NOT NULL
            AND paragraph_key <> ''
        )
    )
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci
  ROW_FORMAT=DYNAMIC;

CREATE TABLE reader_comment_reactions (
    comment_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    user_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    like_count TINYINT UNSIGNED NOT NULL DEFAULT 1,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (comment_id, user_id),
    KEY idx_reader_comment_reaction_user (user_id, created_at),
    CONSTRAINT fk_reader_comment_reaction_comment
        FOREIGN KEY (comment_id) REFERENCES reader_comments(id)
        ON DELETE CASCADE,
    CONSTRAINT chk_reader_comment_reaction_count
        CHECK (like_count BETWEEN 1 AND 3)
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci;

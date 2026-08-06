-- Serialize title+author deduplication across every logical library/source.
-- This side table avoids a blocking unique-index build on the large books
-- table and preserves existing historical rows for explicit audit/cleanup.

CREATE TABLE IF NOT EXISTS global_book_identity_claims (
    identity_hash BINARY(32) NOT NULL,
    identity_key VARCHAR(768) NOT NULL,
    catalog_id BIGINT UNSIGNED NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (identity_hash),
    UNIQUE KEY uq_global_book_identity_claims_catalog (catalog_id),
    CONSTRAINT fk_global_book_identity_claims_book
        FOREIGN KEY (catalog_id) REFERENCES books(id) ON DELETE CASCADE
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci
  ROW_FORMAT=DYNAMIC;

-- Pick the same canonical winner used by the existing body deduplicator.
-- Existing duplicate books are not mutated or deleted by this migration.
INSERT IGNORE INTO global_book_identity_claims (
    identity_hash, identity_key, catalog_id
)
SELECT identity_hash, identity_key, id
FROM (
    SELECT
        identity_hash,
        identity_key,
        id,
        ROW_NUMBER() OVER (
            PARTITION BY identity_hash
            ORDER BY body_available DESC, bytes DESC, id DESC
        ) AS identity_rank
    FROM books
    WHERE is_active=1
      AND identity_key IS NOT NULL
      AND identity_key<>''
) ranked
WHERE identity_rank=1;

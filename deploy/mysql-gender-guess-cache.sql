CREATE TABLE IF NOT EXISTS gender_guess_cache (
    name_key BINARY(32) NOT NULL,
    canonical_name VARCHAR(120) NOT NULL,
    gender VARCHAR(10) NOT NULL DEFAULT 'unknown',
    confidence DECIMAL(5,4) NOT NULL DEFAULT 0.0000,
    provider VARCHAR(40) NOT NULL,
    raw_percent DECIMAL(5,4) NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'success',
    failure_reason VARCHAR(200) NULL,
    lookup_count INT UNSIGNED NOT NULL DEFAULT 0,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    expires_at DATETIME(6) NULL,
    claim_owner VARCHAR(40) NULL,
    claim_until DATETIME(6) NULL,
    PRIMARY KEY (name_key),
    KEY idx_gender_guess_name (canonical_name),
    KEY idx_gender_guess_status_expiry (status, expires_at),
    KEY idx_gender_guess_claim (claim_owner, claim_until)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

ALTER TABLE gender_guess_cache
    MODIFY lookup_count INT UNSIGNED NOT NULL DEFAULT 0;

GRANT SELECT, INSERT, UPDATE ON oohstory_library.gender_guess_cache
    TO 'oohstory_audiobook_role'@'%';

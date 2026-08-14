SET @has_device_hash = (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema=DATABASE()
      AND table_name='audiobook_sessions'
      AND column_name='device_hash'
);
SET @ddl = IF(
    @has_device_hash=0,
    'ALTER TABLE audiobook_sessions ADD COLUMN device_hash BINARY(32) NULL AFTER owner_hash',
    'SELECT 1'
);
PREPARE audiobook_v11_stmt FROM @ddl;
EXECUTE audiobook_v11_stmt;
DEALLOCATE PREPARE audiobook_v11_stmt;

-- Existing sessions predate device-scoped ownership.  Seeding them with the
-- owner hash preserves cancellation semantics until they naturally expire.
UPDATE audiobook_sessions
SET device_hash=owner_hash
WHERE device_hash IS NULL;

SET @device_hash_nullable = (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema=DATABASE()
      AND table_name='audiobook_sessions'
      AND column_name='device_hash'
      AND is_nullable='YES'
);
SET @ddl = IF(
    @device_hash_nullable=1,
    'ALTER TABLE audiobook_sessions MODIFY COLUMN device_hash BINARY(32) NOT NULL',
    'SELECT 1'
);
PREPARE audiobook_v11_stmt FROM @ddl;
EXECUTE audiobook_v11_stmt;
DEALLOCATE PREPARE audiobook_v11_stmt;

SET @has_owner_device_index = (
    SELECT COUNT(*) FROM information_schema.statistics
    WHERE table_schema=DATABASE()
      AND table_name='audiobook_sessions'
      AND index_name='idx_audiobook_sessions_owner_device'
);
SET @ddl = IF(
    @has_owner_device_index=0,
    'ALTER TABLE audiobook_sessions ADD KEY idx_audiobook_sessions_owner_device (owner_hash,device_hash,cancelled,expires_at)',
    'SELECT 1'
);
PREPARE audiobook_v11_stmt FROM @ddl;
EXECUTE audiobook_v11_stmt;
DEALLOCATE PREPARE audiobook_v11_stmt;

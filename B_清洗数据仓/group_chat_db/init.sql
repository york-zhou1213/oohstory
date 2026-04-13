CREATE TABLE IF NOT EXISTS messages_2026_03 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL,
    sender_id TEXT NOT NULL,
    sender_name TEXT,
    chat_id TEXT NOT NULL,
    content TEXT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_sender_2026_03 ON messages_2026_03 (sender_id);
CREATE INDEX IF NOT EXISTS idx_chat_2026_03 ON messages_2026_03 (chat_id);

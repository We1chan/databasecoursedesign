-- Apply after 02-schema.sql in a schema used by the HTTP backend.
CREATE TABLE IF NOT EXISTS auth_session (
    session_digest BINARY(32) PRIMARY KEY,
    account_id BIGINT UNSIGNED NOT NULL,
    expires_at DATETIME(3) NOT NULL,
    KEY ix_auth_session_account (account_id, expires_at),
    KEY ix_auth_session_expiry (expires_at),
    FOREIGN KEY (account_id) REFERENCES user_account (account_id) ON DELETE CASCADE
) ENGINE=InnoDB;

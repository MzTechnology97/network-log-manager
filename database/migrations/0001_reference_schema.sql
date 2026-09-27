CREATE DATABASE IF NOT EXISTS netlog_manager CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE IF NOT EXISTS syslogdb CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE netlog_manager;

CREATE TABLE IF NOT EXISTS roles (
 id SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,
 name VARCHAR(32) NOT NULL,
 description VARCHAR(255) DEFAULT NULL,
 PRIMARY KEY (id), UNIQUE KEY uq_roles_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS users (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 username VARCHAR(64) NOT NULL, password_hash VARCHAR(255) NOT NULL,
 display_name VARCHAR(128) DEFAULT NULL, email VARCHAR(254) DEFAULT NULL,
 enabled TINYINT(1) NOT NULL DEFAULT 1, mfa_enabled TINYINT(1) NOT NULL DEFAULT 0,
 mfa_secret VARBINARY(255) DEFAULT NULL, failed_login_count INT UNSIGNED NOT NULL DEFAULT 0,
 locked_until DATETIME DEFAULT NULL, last_login_at DATETIME DEFAULT NULL,
 password_changed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
 PRIMARY KEY(id), UNIQUE KEY uq_users_username(username), UNIQUE KEY uq_users_email(email)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci ROW_FORMAT=DYNAMIC;

CREATE TABLE IF NOT EXISTS user_roles (
 user_id BIGINT UNSIGNED NOT NULL, role_id SMALLINT UNSIGNED NOT NULL,
 PRIMARY KEY(user_id,role_id), KEY fk_user_roles_role(role_id),
 CONSTRAINT fk_user_roles_role FOREIGN KEY(role_id) REFERENCES roles(id) ON DELETE CASCADE,
 CONSTRAINT fk_user_roles_user FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS sessions (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT, user_id BIGINT UNSIGNED NOT NULL,
 token_hash BINARY(32) NOT NULL, ip_address VARCHAR(45), user_agent VARCHAR(512),
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, last_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 expires_at DATETIME NOT NULL, revoked_at DATETIME DEFAULT NULL,
 PRIMARY KEY(id), UNIQUE KEY uq_sessions_token(token_hash), KEY idx_sessions_user(user_id), KEY idx_sessions_expiry(expires_at),
 CONSTRAINT fk_sessions_user FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS mfa_challenges (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT, user_id BIGINT UNSIGNED NOT NULL,
 token_hash BINARY(32) NOT NULL, purpose ENUM('LOGIN') NOT NULL DEFAULT 'LOGIN',
 attempts TINYINT UNSIGNED NOT NULL DEFAULT 0, max_attempts TINYINT UNSIGNED NOT NULL DEFAULT 5,
 ip_address VARCHAR(45), user_agent VARCHAR(512), created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 expires_at DATETIME NOT NULL, used_at DATETIME DEFAULT NULL,
 PRIMARY KEY(id), UNIQUE KEY uq_mfa_challenge_token(token_hash), KEY idx_mfa_challenge_user(user_id), KEY idx_mfa_challenge_expiry(expires_at),
 CONSTRAINT fk_mfa_challenge_user FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS settings (
 setting_key VARCHAR(128) NOT NULL, setting_value TEXT DEFAULT NULL,
 updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
 PRIMARY KEY(setting_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS audit_log (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT, created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
 user_id BIGINT UNSIGNED DEFAULT NULL, username VARCHAR(64), action VARCHAR(64) NOT NULL, category VARCHAR(32) NOT NULL,
 ip_address VARCHAR(45), user_agent VARCHAR(512), target_type VARCHAR(64), target_id VARCHAR(255),
 success TINYINT(1) NOT NULL DEFAULT 1, details TEXT,
 PRIMARY KEY(id), KEY idx_audit_time(created_at), KEY idx_audit_user_time(user_id,created_at), KEY idx_audit_action_time(action,created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci ROW_FORMAT=DYNAMIC;

CREATE TABLE IF NOT EXISTS archive_catalog (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT, log_date DATE NOT NULL,
 table_name VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
 archive_path VARCHAR(512) NOT NULL, meta_path VARCHAR(512) NOT NULL, sha256_path VARCHAR(512) NOT NULL,
 row_count BIGINT UNSIGNED, compressed_bytes BIGINT UNSIGNED, sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin,
 archived_at DATETIME, retention_days INT UNSIGNED, schema_generation VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin,
 has_nat TINYINT(1) NOT NULL DEFAULT 0, archive_status ENUM('AVAILABLE','INCOMPLETE','INVALID') NOT NULL DEFAULT 'AVAILABLE',
 integrity_verified_at DATETIME, last_scanned_at DATETIME NOT NULL,
 PRIMARY KEY(id), UNIQUE KEY uq_archive_date(log_date), UNIQUE KEY uq_archive_table(table_name), KEY idx_archive_status(archive_status,log_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci ROW_FORMAT=DYNAMIC;

CREATE TABLE IF NOT EXISTS export_jobs (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT, user_id BIGINT UNSIGNED NOT NULL, username VARCHAR(64) NOT NULL,
 status ENUM('PENDING','RUNNING','COMPLETED','FAILED','CANCELLED') NOT NULL DEFAULT 'PENDING',
 start_time DATETIME(6) NOT NULL, end_time DATETIME(6) NOT NULL,
 source_ip VARCHAR(45), source_port SMALLINT UNSIGNED, nat_source_ip VARCHAR(45), nat_source_port SMALLINT UNSIGNED,
 dest_ip VARCHAR(45), dest_port SMALLINT UNSIGNED, protocol VARCHAR(5),
 progress_rows BIGINT UNSIGNED NOT NULL DEFAULT 0, progress_day DATE, result_rows BIGINT UNSIGNED,
 online_days INT UNSIGNED NOT NULL DEFAULT 0, archive_days INT UNSIGNED NOT NULL DEFAULT 0,
 filename VARCHAR(255), file_size BIGINT UNSIGNED, file_sha256 CHAR(64), error_message TEXT,
 requested_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3), started_at DATETIME(3), completed_at DATETIME(3),
 PRIMARY KEY(id), KEY idx_export_user_time(user_id,requested_at), KEY idx_export_status_time(status,requested_at),
 CONSTRAINT fk_export_jobs_user FOREIGN KEY(user_id) REFERENCES users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci ROW_FORMAT=DYNAMIC;

INSERT INTO roles(id,name,description) VALUES
(1,'Administrator','Full administration'),
(2,'Operator','Operational access'),
(3,'Auditor','Read-only audit access')
ON DUPLICATE KEY UPDATE name=VALUES(name),description=VALUES(description);

INSERT INTO settings(setting_key,setting_value) VALUES
('app_name','Network Log Manager'),('failed_login_limit','5'),('lock_minutes','15'),('session_minutes','60'),('timezone','Europe/Rome')
ON DUPLICATE KEY UPDATE setting_value=setting_value;

USE netlog_manager;

CREATE TABLE IF NOT EXISTS storage_targets (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  name VARCHAR(120) NOT NULL,
  storage_type ENUM('LOCAL','SMB','SFTP','S3') NOT NULL,
  role ENUM('PRIMARY','REPLICA') NOT NULL DEFAULT 'REPLICA',
  enabled TINYINT(1) NOT NULL DEFAULT 1,
  read_fallback TINYINT(1) NOT NULL DEFAULT 1,
  host VARCHAR(255) NULL,
  port INT NULL,
  share_name VARCHAR(255) NULL,
  endpoint_url VARCHAR(1024) NULL,
  bucket_name VARCHAR(255) NULL,
  region_name VARCHAR(128) NULL,
  base_path VARCHAR(1024) NOT NULL DEFAULT '',
  folder VARCHAR(1024) NOT NULL DEFAULT '',
  domain_name VARCHAR(255) NULL,
  secret_encrypted LONGTEXT NULL,
  health_status ENUM('UNKNOWN','HEALTHY','DEGRADED','OFFLINE') NOT NULL DEFAULT 'UNKNOWN',
  health_failures INT UNSIGNED NOT NULL DEFAULT 0,
  last_health_at DATETIME(3) NULL,
  last_success_at DATETIME(3) NULL,
  last_error TEXT NULL,
  latency_ms DECIMAL(12,2) NULL,
  free_bytes BIGINT UNSIGNED NULL,
  total_bytes BIGINT UNSIGNED NULL,
  test_status ENUM('NOT_TESTED','OK','FAILED') NOT NULL DEFAULT 'NOT_TESTED',
  tested_at DATETIME(3) NULL,
  test_sha256 CHAR(64) NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY(id),
  UNIQUE KEY uq_storage_name(name),
  KEY idx_storage_role_enabled(role,enabled),
  KEY idx_storage_health(health_status,last_health_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS storage_replication_state (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  storage_id BIGINT UNSIGNED NOT NULL,
  relative_path VARCHAR(1536) NOT NULL,
  sha256 CHAR(64) NULL,
  status ENUM('PENDING','SYNCED','FAILED') NOT NULL DEFAULT 'PENDING',
  attempts INT UNSIGNED NOT NULL DEFAULT 0,
  last_attempt_at DATETIME(3) NULL,
  synced_at DATETIME(3) NULL,
  last_error TEXT NULL,
  PRIMARY KEY(id),
  UNIQUE KEY uq_storage_object(storage_id,relative_path),
  KEY idx_replication_status(status,last_attempt_at),
  CONSTRAINT fk_replication_storage FOREIGN KEY(storage_id) REFERENCES storage_targets(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO storage_targets(name,storage_type,role,enabled,read_fallback,base_path,test_status,health_status)
SELECT 'Storage locale','LOCAL','PRIMARY',1,1,'/archive/mikrotik','OK','UNKNOWN'
WHERE NOT EXISTS (SELECT 1 FROM storage_targets);

INSERT INTO settings(setting_key,setting_value) VALUES
 ('storage_health_interval_seconds','60'),
 ('storage_health_warning_failures','1'),
 ('storage_health_critical_failures','3')
ON DUPLICATE KEY UPDATE setting_value=setting_value;

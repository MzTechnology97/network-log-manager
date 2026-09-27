USE netlog_manager;

CREATE TABLE IF NOT EXISTS notification_channels (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 name VARCHAR(128) NOT NULL,
 channel_type ENUM('WEBHOOK','EMAIL','TELEGRAM','SLACK','DISCORD') NOT NULL,
 enabled TINYINT(1) NOT NULL DEFAULT 1,
 configuration_json LONGTEXT NOT NULL,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
 PRIMARY KEY(id),
 KEY idx_notification_channels_enabled(enabled,channel_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci ROW_FORMAT=DYNAMIC;

CREATE TABLE IF NOT EXISTS system_alerts (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 alert_key VARCHAR(128) NOT NULL,
 severity ENUM('INFO','WARNING','CRITICAL') NOT NULL,
 title VARCHAR(255) NOT NULL,
 message TEXT NOT NULL,
 first_seen_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
 last_seen_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
 resolved_at DATETIME(3) DEFAULT NULL,
 notification_sent_at DATETIME(3) DEFAULT NULL,
 occurrence_count BIGINT UNSIGNED NOT NULL DEFAULT 1,
 PRIMARY KEY(id),
 KEY idx_system_alerts_open(resolved_at,severity,last_seen_at),
 KEY idx_system_alerts_key(alert_key,last_seen_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci ROW_FORMAT=DYNAMIC;

INSERT INTO settings(setting_key,setting_value) VALUES
 ('retention_days','1825'),
 ('archive_after_days','365'),
 ('storage_warning_percent','80'),
 ('storage_critical_percent','90'),
 ('ingestion_stale_minutes','5'),
 ('database_check_enabled','1'),
 ('alert_repeat_minutes','60'),
 ('monitor_interval_seconds','60'),
 ('external_storage_enabled','0'),
 ('external_storage_type','LOCAL'),
 ('external_storage_path','/archive/mikrotik'),
 ('external_storage_host',''),
 ('external_storage_share',''),
 ('external_storage_mount_options',''),
 ('external_storage_healthcheck','1')
ON DUPLICATE KEY UPDATE setting_value=setting_value;

USE netlog_manager;

CREATE TABLE IF NOT EXISTS notification_delivery_log (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  alert_id BIGINT UNSIGNED NULL,
  channel_id BIGINT UNSIGNED NOT NULL,
  event_type VARCHAR(80) NOT NULL,
  status ENUM('SENT','FAILED') NOT NULL,
  attempted_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  error_message TEXT NULL,
  PRIMARY KEY(id),
  KEY idx_delivery_channel_time(channel_id,attempted_at),
  KEY idx_delivery_status_time(status,attempted_at),
  CONSTRAINT fk_delivery_alert FOREIGN KEY(alert_id) REFERENCES system_alerts(id) ON DELETE SET NULL,
  CONSTRAINT fk_delivery_channel FOREIGN KEY(channel_id) REFERENCES notification_channels(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

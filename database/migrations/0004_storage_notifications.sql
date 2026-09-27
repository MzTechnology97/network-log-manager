USE netlog_manager;

ALTER TABLE notification_channels
  ADD COLUMN IF NOT EXISTS event_types_json LONGTEXT NULL AFTER secret_json;

INSERT INTO settings(setting_key,setting_value) VALUES
 ('external_storage_enabled','0'),
 ('external_storage_type','LOCAL'),
 ('external_storage_host',''),
 ('external_storage_port',''),
 ('external_storage_share',''),
 ('external_storage_path','/archive/mikrotik'),
 ('external_storage_folder',''),
 ('external_storage_domain',''),
 ('external_storage_secret',''),
 ('external_storage_test_status','NOT_TESTED'),
 ('external_storage_tested_at',''),
 ('external_storage_test_sha256','')
ON DUPLICATE KEY UPDATE setting_value=setting_value;

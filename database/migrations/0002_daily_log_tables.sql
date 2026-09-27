USE syslogdb;

DROP PROCEDURE IF EXISTS CreateFutureTables;
DELIMITER //
CREATE PROCEDURE CreateFutureTables()
BEGIN
    DECLARE i INT DEFAULT 1;
    WHILE i < 7 DO
        SET @table_date = DATE_FORMAT(DATE_ADD(CURDATE(), INTERVAL i DAY), '%Y_%m_%d');
        SET @table_name = CONCAT('mikrotik_logs_', @table_date);
        SET @query = CONCAT(
          'CREATE TABLE IF NOT EXISTS syslogdb.`', @table_name, '` (',
          '`id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,',
          '`timestamp` DATETIME(3) NOT NULL,',
          '`source_ip` VARCHAR(15) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,',
          '`source_port` SMALLINT UNSIGNED NOT NULL,',
          '`dest_ip` VARCHAR(15) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,',
          '`dest_port` SMALLINT UNSIGNED NOT NULL,',
          '`protocol` VARCHAR(5) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,',
          '`nat_source_ip` VARCHAR(15) CHARACTER SET ascii COLLATE ascii_bin NULL,',
          '`nat_source_port` SMALLINT UNSIGNED NULL,',
          'PRIMARY KEY (`id`),',
          'KEY `idx_nat_time` (`nat_source_ip`,`nat_source_port`,`timestamp`),',
          'KEY `idx_natport_time` (`nat_source_port`,`timestamp`),',
          'KEY `idx_source_time` (`source_ip`,`timestamp`)',
          ') ENGINE=InnoDB ROW_FORMAT=DYNAMIC'
        );
        PREPARE stmt FROM @query; EXECUTE stmt; DEALLOCATE PREPARE stmt;
        SET i = i + 1;
    END WHILE;
END//
DELIMITER ;

DROP EVENT IF EXISTS AutoCreateTables;
CREATE EVENT AutoCreateTables
ON SCHEDULE EVERY 1 DAY
STARTS (CURRENT_DATE + INTERVAL 23 HOUR + INTERVAL 55 MINUTE)
ON COMPLETION NOT PRESERVE ENABLE
DO CALL CreateFutureTables();

CALL CreateFutureTables();

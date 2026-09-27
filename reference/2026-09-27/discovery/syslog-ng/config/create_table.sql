DELIMITER $$

CREATE PROCEDURE CreateLogTableIfNotExists(IN table_name VARCHAR(255))
BEGIN
    SET @query = CONCAT(
        'CREATE TABLE IF NOT EXISTS ', table_name, ' (
            id INT AUTO_INCREMENT PRIMARY KEY,
            timestamp DATETIME NOT NULL,
            source_ip VARCHAR(45),
            source_port INT,
            dest_ip VARCHAR(45),
            dest_port INT,
            mac_address VARCHAR(17),
            protocol VARCHAR(10),
            raw_log TEXT
        );'
    );
    PREPARE stmt FROM @query;
    EXECUTE stmt;
    DEALLOCATE PREPARE stmt;
END$$

DELIMITER ;

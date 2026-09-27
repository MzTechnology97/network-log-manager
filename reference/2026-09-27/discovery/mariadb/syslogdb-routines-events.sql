/*M!999999\- enable the sandbox mode */ 
-- MariaDB dump 10.19  Distrib 10.11.18-MariaDB, for debian-linux-gnu (x86_64)
--
-- Host: localhost    Database: syslogdb
-- ------------------------------------------------------
-- Server version	10.11.18-MariaDB-0+deb12u1

/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;
/*!40101 SET @OLD_COLLATION_CONNECTION=@@COLLATION_CONNECTION */;
/*!40101 SET NAMES utf8mb4 */;
/*!40103 SET @OLD_TIME_ZONE=@@TIME_ZONE */;
/*!40103 SET TIME_ZONE='+00:00' */;
/*!40014 SET @OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS, FOREIGN_KEY_CHECKS=0 */;
/*!40101 SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='NO_AUTO_VALUE_ON_ZERO' */;
/*!40111 SET @OLD_SQL_NOTES=@@SQL_NOTES, SQL_NOTES=0 */;

--
-- Dumping events for database 'syslogdb'
--
/*!50106 SET @save_time_zone= @@TIME_ZONE */ ;
/*!50106 DROP EVENT IF EXISTS `AutoCreateTables` */;
DELIMITER ;;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;;
/*!50003 SET character_set_client  = utf8mb3 */ ;;
/*!50003 SET character_set_results = utf8mb3 */ ;;
/*!50003 SET collation_connection  = utf8mb3_general_ci */ ;;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;;
/*!50003 SET sql_mode              = 'STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_AUTO_CREATE_USER,NO_ENGINE_SUBSTITUTION' */ ;;
/*!50003 SET @saved_time_zone      = @@time_zone */ ;;
/*!50003 SET time_zone             = 'SYSTEM' */ ;;
/*!50106 CREATE*/ /*!50117 DEFINER=`netlog_maintenance`@`localhost`*/ /*!50106 EVENT `AutoCreateTables` ON SCHEDULE EVERY 1 DAY STARTS '2026-09-22 23:55:00' ON COMPLETION NOT PRESERVE ENABLE DO CALL CreateFutureTables() 
*/ ;;
/*!50003 SET time_zone             = @saved_time_zone */ ;;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;;
/*!50003 SET character_set_client  = @saved_cs_client */ ;;
/*!50003 SET character_set_results = @saved_cs_results */ ;;
/*!50003 SET collation_connection  = @saved_col_connection */ ;;
DELIMITER ;
/*!50106 SET TIME_ZONE= @save_time_zone */ ;

--
-- Dumping routines for database 'syslogdb'
--
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_AUTO_CREATE_USER,NO_ENGINE_SUBSTITUTION' */ ;
/*!50003 DROP PROCEDURE IF EXISTS `CreateFutureTables` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb3 */ ;
/*!50003 SET character_set_results = utf8mb3 */ ;
/*!50003 SET collation_connection  = utf8mb3_general_ci */ ;
DELIMITER ;;
CREATE DEFINER=`netlog_maintenance`@`localhost` PROCEDURE `CreateFutureTables`()
BEGIN
    DECLARE i INT DEFAULT 1;

    WHILE i < 7 DO

        SET @table_date =
            DATE_FORMAT(
                DATE_ADD(CURDATE(), INTERVAL i DAY),
                '%Y_%m_%d'
            );

        SET @table_name =
            CONCAT(
                'mikrotik_logs_',
                @table_date
            );

        SET @query = CONCAT(
            'CREATE TABLE IF NOT EXISTS syslogdb.`',
            @table_name,
            '` (
                `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

                `timestamp` DATETIME(3) NOT NULL,

                `source_ip` VARCHAR(15)
                    CHARACTER SET ascii
                    COLLATE ascii_bin NOT NULL,

                `source_port`
                    SMALLINT UNSIGNED NOT NULL,

                `dest_ip` VARCHAR(15)
                    CHARACTER SET ascii
                    COLLATE ascii_bin NOT NULL,

                `dest_port`
                    SMALLINT UNSIGNED NOT NULL,

                `protocol` VARCHAR(5)
                    CHARACTER SET ascii
                    COLLATE ascii_bin NOT NULL,

                `nat_source_ip` VARCHAR(15)
                    CHARACTER SET ascii
                    COLLATE ascii_bin NULL,

                `nat_source_port`
                    SMALLINT UNSIGNED NULL,

                PRIMARY KEY (`id`),

                KEY `idx_nat_time`
                    (
                        `nat_source_ip`,
                        `nat_source_port`,
                        `timestamp`
                    ),

                KEY `idx_natport_time`
                    (
                        `nat_source_port`,
                        `timestamp`
                    ),

                KEY `idx_source_time`
                    (
                        `source_ip`,
                        `timestamp`
                    )
            )
            ENGINE=InnoDB
            ROW_FORMAT=DYNAMIC'
        );

        PREPARE stmt FROM @query;
        EXECUTE stmt;
        DEALLOCATE PREPARE stmt;

        SET i = i + 1;

    END WHILE;
END
;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!40103 SET TIME_ZONE=@OLD_TIME_ZONE */;

/*!40101 SET SQL_MODE=@OLD_SQL_MODE */;
/*!40014 SET FOREIGN_KEY_CHECKS=@OLD_FOREIGN_KEY_CHECKS */;
/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
/*!40111 SET SQL_NOTES=@OLD_SQL_NOTES */;

-- Dump completed on 2026-09-27 10:00:10

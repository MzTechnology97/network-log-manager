# Backup, restore e aggiornamenti / Backup, Restore and Updates

Gli aggiornamenti eseguono backup prima del deploy e health-check dopo. Il rollback software non inverte migration DB. / Updates create a backup before deployment and run a post-deploy health check. Software rollback does not reverse DB migrations.

Il backup della piattaforma contiene configurazione, database applicativo e schema/routine/eventi di `syslogdb`; l'archivio storico deve essere protetto separatamente. / Platform backup contains configuration, application DB and `syslogdb` schema/routines/events; historical archives require separate protection.

Vedi / See: [docs/updates-recovery.md](../docs/updates-recovery.md).

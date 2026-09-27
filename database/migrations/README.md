# Database migrations

Migrations are immutable, ordered SQL files. Once a migration has been applied its checksum is recorded in `netlog_manager.schema_migrations`.

Rules:

1. Never edit an applied migration.
2. Application releases must remain compatible with the database state reached by their migrations.
3. Updates back up `netlog_manager` and the `syslogdb` schema before applying migrations.
4. Large historical log tables and archive files are never deleted by the updater.
5. Destructive migrations require an explicit maintenance release and a tested restore procedure.

# Database and retention

## Databases

`netlog_manager` stores users, roles, sessions, MFA challenges, settings, audit records, archive catalog data, export jobs and migration state.

`syslogdb` stores date-partitioned-by-table connection logs such as `mikrotik_logs_YYYY_MM_DD`.

## Current log table

Current production tables use BIGINT IDs, DATETIME(3), compact IPv4/port columns and indexes for NAT/time and source/time investigations.

## Least privilege

- `netlog_app`: CRUD on application database.
- `netlog_reader`: SELECT on log database.
- `netlog_ingest`: INSERT on log database.
- `netlog_maintenance`: CREATE plus EXECUTE of the future-table procedure.

## Future tables

MariaDB's event scheduler runs `CreateFutureTables()` daily. The procedure prepares today plus six future daily tables. The maintenance account is the explicit routine/event definer.

## Retention

The reference archive policy checks daily but only considers tables from years before the current year. The reference service processes a limited batch per run. Archive files live outside the database and application release lifecycle.

# Architecture

Network Log Manager separates ingestion, storage, historical retention and the web application.

## Data path

1. Network equipment sends syslog over UDP/TCP.
2. syslog-ng parses connection fields and writes a human-readable live log.
3. The SQL destination inserts records into a date-specific MariaDB table.
4. `CreateFutureTables()` keeps today and the next six tables ready.
5. The archive process compresses eligible prior-year tables to Zstandard files and verifies them before the online table is removed.
6. The archive catalog describes available historical days and schema capabilities.
7. Historical cache files are rebuildable acceleration data, not authoritative storage.
8. FastAPI provides dashboard, search, user administration and export workflows.
9. Native deployments expose FastAPI only on loopback and Apache terminates HTTPS.

## Trust boundaries

The web application does not use MariaDB root. Separate identities exist for application CRUD, read-only log queries, syslog ingestion and table maintenance. Permanent archive storage is independent of application releases.

## Schema generations

Historical archives may use older schemas. In particular, legacy archives can lack NAT fields. A NAT-filtered historical query must therefore report those days as unsupported rather than interpreting them as zero matches.


## Resilient archive storage

Archive storage is registry-based. A PRIMARY target is authoritative and zero or more REPLICA targets can hold independently verified copies. Supported target backends are LOCAL, SMB, SFTP and S3-compatible storage. Reads resolve the primary first and can use enabled verified replicas as fallback. The historical cache remains disposable.

Periodic monitoring records storage health separately from the one-time configuration test. Replication state is tracked per archive object and replica. See [Storage, replication and monitoring](storage-monitoring.md).

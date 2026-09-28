# Operazioni / Operations

Health check: `/opt/netlog-manager/scripts/healthcheck.sh`.

Docker include timer per archivio e rotazione live log; worker dedicati gestiscono cache storico, export e cleanup. / Docker includes archive and live-log rotation timers; dedicated workers handle history cache, exports and cleanup.

Vedi / See: [docs/operations.md](../docs/operations.md).


Il monitor verifica health storage, capacità, replica, freschezza ingest, database e listener syslog. Le consegne automatiche sono tracciate come SENT/FAILED. / The monitor checks storage health/capacity/replication, ingestion freshness, database and syslog listener; automatic deliveries are audited as SENT/FAILED.

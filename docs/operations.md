# Operations

## Services in native mode

Core services are MariaDB, syslog-ng, Network Log Manager/Uvicorn and Apache. Additional systemd jobs handle archive caching, exports, cleanup and updates.

Useful checks:

```bash
systemctl status netlog-manager
systemctl status syslog-ng
systemctl status mariadb
systemctl status apache2
```

Run the product health check with:

```bash
sudo /opt/netlog-manager/scripts/healthcheck.sh
```

## Search

Advanced Search requires a bounded time range and at least one meaningful filter. Online and archive sources can be combined. The GUI result limit is intentionally bounded; large retrievals should use the export workflow.

## Exports

Exports run asynchronously. A worker streams matching rows into CSV, records progress and calculates SHA-256. Completed files are temporary artifacts and are cleaned according to the export cleanup policy.

## Historical cache

The cache under `/var/cache/netlog-manager/history` is disposable. Deleting it does not delete the permanent archive; it can be rebuilt from valid archive files.

## Archive integrity

Archive files are authoritative historical retention. The archive workflow validates compressed data and checksum metadata before dropping an eligible source table. Never manually remove an online table merely because an archive filename exists.

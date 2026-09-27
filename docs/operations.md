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


## Operational monitoring and alerts / Monitoraggio operativo e alert

Administrators can configure operational thresholds from **Impostazioni**. The monitoring worker checks archive storage capacity, database reachability, syslog listener reachability and the age of the newest ingested record. Open and resolved events are stored in the application database and repeated notifications are rate-limited.

Gli amministratori possono configurare le soglie dalla pagina **Impostazioni**. Il worker controlla capacità dello storage archivio, raggiungibilità del database e del listener syslog e l'età dell'ultimo record acquisito. Gli eventi attivi/risolti vengono conservati nel database applicativo e la ripetizione delle notifiche è temporizzata.

Supported notification transports are generic webhook, SMTP e-mail, Telegram, Slack and Discord. Channel credentials are stored separately from the visible channel configuration and are never rendered back by the settings page.

## Retention and archive age / Retention e archiviazione

`archive_after_days` determines when online daily tables become eligible for compressed archive. `retention_days` determines when verified compressed archives become eligible for final deletion. The archive job validates the compressed stream and checksum before dropping the online table; the retention job requires archive metadata and a valid SHA-256 checksum before deleting an expired archive.

`archive_after_days` determina quando le tabelle giornaliere online diventano archiviabili. `retention_days` determina quando gli archivi compressi verificati possono essere eliminati definitivamente. Impostare la retention in base agli obblighi normativi e contrattuali applicabili: il valore predefinito del prodotto non costituisce una determinazione legale.

## External storage / Storage esterno

The archive root is intentionally presented to containers as a bind mount. For local disks, NFS and SMB/CIFS, mount the filesystem on the Docker host and use that mount point as `ARCHIVE_ROOT`. This design avoids granting the web application privileged host-mount capabilities.

La directory archivio viene esposta ai container tramite bind mount. Per dischi locali, NFS e SMB/CIFS, il filesystem deve essere montato sull'host Docker e il relativo mount point usato come `ARCHIVE_ROOT`. In questo modo la GUI non riceve privilegi di mount sull'host.

SFTP and S3-compatible targets are accessed by the storage backend and are not mounted directly by the web container. Storage targets are integrity-tested before activation, replicas are tracked independently, and verified replicas can provide read fallback. Automatic notification delivery attempts are recorded as SENT/FAILED for diagnosis.

See [Storage, replication and monitoring](storage-monitoring.md) for roles, health states, replication, fallback and notification event types.

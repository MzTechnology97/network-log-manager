# Network Log Manager

Network Log Manager is a self-hosted platform for collecting, retaining, searching and exporting network connection logs. The current implementation is based on the production reference installation under `reference/2026-09-27/`.

## Implemented features

- MikroTik TCP/UDP syslog ingestion through syslog-ng.
- Parsing of protocol, source/destination IP and ports, plus SNAT public IP/port when present.
- Daily MariaDB log tables with automatic creation of today + six future tables.
- Indexed online search optimized for NAT attribution and source-IP investigations.
- Historical compressed archives (`.sql.zst`) with catalog and integrity metadata.
- Hybrid Advanced Search across online MariaDB data and historical cache/archive data.
- Asynchronous CSV exports with progress, SHA-256 and expiry/cleanup.
- Live Log view.
- Dashboard and log explorer.
- Local users, Argon2id passwords, TOTP MFA, lockout, server-side sessions and CSRF protection.
- Roles: Administrator, Operator and Auditor.
- Audit logging.
- HTTPS reverse proxy through Apache in native mode.
- Least-privilege MariaDB service accounts.
- Backup-before-update, immutable migrations and application health checks.
- Stable / candidate / development update channels.
- Native Debian deployment and Docker Compose deployment framework.

## Architecture

```text
Network devices
      |
      | syslog UDP/TCP
      v
   syslog-ng --------> /var/log/network.log
      |
      v
 MariaDB / syslogdb
      |
      +---- recent daily tables
      |
      +----> archive job ----> /archive/mikrotik/YYYY/MM/*.sql.zst
                                  |
                                  v
                         disposable history cache
                                  |
                     +------------+-------------+
                     |                          |
                Advanced Search             CSV Export
                     |                          |
                     +------------+-------------+
                                  |
                           FastAPI application
                                  |
                             Apache HTTPS
```

## Storage model

| Path | Purpose | Persistent |
|---|---|---|
| `/archive/mikrotik` | Long-term compressed log archive | **Yes** |
| `/var/lib/netlog-manager` | Application state | Yes |
| `/var/cache/netlog-manager/history` | Rebuildable historical cache | No |
| `/var/cache/netlog-manager/exports` | Generated exports | Temporary |
| `/var/log/network.log` | Live parsed log | Operational |
| `/etc/netlog-manager` | Installation configuration/secrets | **Yes, protected** |

The archive must never be treated as disposable container storage. For NAS deployments it should normally be a host-mounted NFS/SMB filesystem.

## Native installation

Target: clean Debian 12/13 host.

```bash
sudo ./scripts/bootstrap.sh
```

Choose **Native Debian installation**. The installer generates internal database credentials, configures MariaDB, syslog-ng, the Python virtualenv, systemd services, Apache HTTPS and then starts the guided Administrator creation.

The initial self-signed certificate is suitable for initial/internal deployment. HSTS is intentionally disabled until a trusted certificate is installed.

## Docker

Choose **Docker Compose** from the same bootstrap. Docker support is being finalized on the `bootstrap-installer` branch; do not use it as a production deployment until the branch is released.

Persistent database data and archives are deliberately kept outside application images.

## Updates

Manual update:

```bash
sudo /opt/netlog-manager/scripts/update.sh
```

Update sequence:

```text
fetch release -> backup -> migrations -> deploy -> health check
                                              |
                                              +-- failure: application rollback
```

Database migrations are forward-only and are **not** automatically reversed. Updates never intentionally replace MariaDB data volumes or the permanent archive.

Channels: `stable`, `candidate`, `development`. Automatic updates are installed but remain disabled by default until explicitly enabled.

## Security model

Application, reader, ingestion and maintenance database identities are separate. Secrets are generated locally and must never be committed. The GitHub repository is not a secret store. Customer update credentials should be read-only.

## Documentation

Detailed documentation is maintained in `docs/` and can also be published into the GitHub Wiki:

- [Architecture](docs/architecture.md)
- [Configuration and files](docs/configuration.md)
- [Operations](docs/operations.md)
- [Database and retention](docs/database-retention.md)
- [Updates and recovery](docs/updates-recovery.md)

## Development

Pull requests run CI checks for Bash/Python syntax, dependency installation, migration naming, secret/private-key detection and Docker Compose validation.

`reference/2026-09-27/` is a sanitized discovery snapshot of the original working installation. It is documentation/reference material and is not deployed directly.

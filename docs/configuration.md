# Configuration and files

## Product configuration

`/etc/netlog-manager/install.env` contains non-secret installation settings such as deployment mode, hostname, syslog port, timezone and archive root.

`/etc/netlog-manager/secrets.env` contains generated service credentials and must remain root-readable only.

Native application runtime configuration is written to `/opt/netlog-manager/config/app.env` with group access for the dedicated `netlog` account.

## Important paths

- `/opt/netlog-manager/app`: FastAPI source.
- `/opt/netlog-manager/templates`: Jinja templates.
- `/opt/netlog-manager/venv`: Python environment.
- `/var/lib/netlog-manager`: application/update state.
- `/var/cache/netlog-manager/history`: disposable archive cache.
- `/var/cache/netlog-manager/exports`: generated CSV files.
- `/archive/mikrotik`: permanent archive by default.
- `/var/log/network.log`: parsed live stream.

## Environment variables

The application recognizes `NETLOG_DB_HOST`, `NETLOG_DB_USER`, `NETLOG_DB_PASSWORD`, `NETLOG_DB_NAME`, `SYSLOG_DB_HOST`, `SYSLOG_DB_USER`, `SYSLOG_DB_PASSWORD`, `SYSLOG_DB_NAME`, `SECRET_KEY`, `ARCHIVE_ROOT`, `EXPORT_ROOT` and `LIVE_LOG`.

`NETLOG_ENV_FILE` can override the default native app.env path. This is mainly useful for packaging and containers.

## TLS

Native installation initially creates a local self-signed certificate under `/etc/ssl/netlog-manager/`. Replace it with a trusted certificate where appropriate. HSTS is intentionally not enabled by default.

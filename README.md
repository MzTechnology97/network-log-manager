# Network Log Manager

Network Log Manager centralizes network connection logs, historical archives, searches and exports.

## Installation

Run the guided bootstrap as root on a fresh Debian 12/13 host:

```bash
./scripts/bootstrap.sh
```

The wizard supports:

- **Docker** deployment using Docker Compose.
- **Native** deployment using system packages, Python virtualenv and systemd.

Configuration and credentials are generated locally and are never committed to Git.

## Updates

```bash
sudo ./scripts/update.sh
```

Updates use a backup -> migration -> deployment -> health-check sequence. Database and archive data live outside application releases and are never replaced by an application update.

Automatic updates can be enabled by the installer. Production deployments follow the configured update channel.

## Reference

`reference/2026-09-27/` is the sanitized discovery snapshot of the original working installation and is not deployed directly.

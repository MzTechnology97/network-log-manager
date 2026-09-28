# Storage, replication and monitoring

This document describes the operational storage model introduced for resilient archive retention.

## Storage roles

Network Log Manager keeps a registry of archive targets. The default target is **Storage locale**, type `LOCAL`, path `/archive/mikrotik`. It is always recoverable from the settings page and can be restored as PRIMARY.

A target can be:

- `PRIMARY`: authoritative destination used for archive writes.
- `REPLICA`: independent copy used for redundancy and, when enabled, read fallback.

Supported target types are `LOCAL`, `SMB`, `SFTP` and S3-compatible object storage. Remote credentials are stored encrypted and are not rendered back in the UI.

## Activation and integrity test

A storage target must pass an integrity test before it is considered usable. The test writes a temporary object, reads it back, verifies SHA-256 and removes it. A successful configuration test is recorded separately from periodic health status.

Health states are `UNKNOWN`, `HEALTHY`, `DEGRADED` and `OFFLINE`. The registry records last check, last success, failure count, latency/error information and capacity where the backend exposes it reliably.

For Docker deployments the local archive root is a host bind mount. The application/monitor runtime uses UID 10001 and GID 999; installers and updates create/repair only the archive root with mode 0750. Existing archive contents are not recursively re-owned during updates.

## Replication and fallback

Replication state is tracked per archive object and replica. Copies are verified before they are considered successful. A replica must not cause deletion of the authoritative source merely because a remote filename exists.

Historical reads resolve the PRIMARY first and may fall back to enabled replicas marked for read fallback. Historical cache under `/var/cache/netlog-manager/history` is disposable acceleration data and is never authoritative.

## Monitoring

The monitor checks storage health and integrity, local capacity, recent replication failures, ingestion freshness, database availability and syslog listener reachability.

Alert event keys are:

- `storage_health`
- `storage_replication`
- `storage_capacity`
- `storage_unavailable`
- `ingestion_stale`
- `database_unavailable`
- `syslog_listener_down`

Open alerts are updated while the condition persists and resolved when the condition disappears. When an open condition recovers, the same channels subscribed to that event type receive a one-shot `RECOVERY` notification. Recovery attempts are audited with the `:recovery` suffix in `notification_delivery_log`. Repeated incident delivery is rate-limited by `alert_repeat_minutes`.

Notification transports: Telegram, e-mail/SMTP, Slack, Discord and generic webhook. Each channel can subscribe to selected event types. Automatic delivery attempts are persisted in `notification_delivery_log` as `SENT` or `FAILED`, including the error text for failed attempts.

## Operational verification

From the UI, use **Impostazioni → Storage archivio → Test**. For the default local storage a healthy target should show **Attivo**, a recent successful check, capacity/free space and no last error.

Docker operators can additionally verify the runtime identity and bind-mount write permission:

```bash
docker-compose --env-file /var/lib/netlog-manager/docker/.env exec -T monitor \
  sh -c 'id; touch /archive/mikrotik/.permission-test && rm /archive/mikrotik/.permission-test'
```

Do not use recursive ownership changes on the archive as a routine repair operation.

## Failure handling

If PRIMARY is unavailable, do not delete source data or assume a replica is complete. Investigate the storage health error and replication state. Read fallback is intended to preserve access to verified historical objects; it does not turn cache files into authoritative storage.

If automatic notifications appear missing, inspect the channel's **Ultima consegna automatica** status and `notification_delivery_log`. A manual channel test proves credentials/connectivity from the application path, while automatic delivery is performed by the monitoring runtime.

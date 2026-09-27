# Storage, replica e monitoraggio / Storage, Replication and Monitoring

La configurazione completa e le procedure operative sono mantenute in [docs/storage-monitoring.md](../docs/storage-monitoring.md).

## Sintesi

- `Storage locale` è il target LOCAL predefinito e ripristinabile.
- Un target PRIMARY è autorevole; le REPLICA sono copie indipendenti.
- Backend supportati: LOCAL, SMB, SFTP e S3-compatible.
- Prima dell'attivazione viene eseguito un test write → read → SHA-256 → delete.
- Le repliche verificate possono essere abilitate come fallback di lettura.
- Il monitor controlla health, capacità, replica, ingest, database e listener syslog.
- Telegram, e-mail, Slack, Discord e webhook possono sottoscrivere eventi selezionati.
- Ogni consegna automatica viene registrata come SENT o FAILED.

## Summary

The default local target is recoverable, PRIMARY is authoritative, replicas are independently verified, and verified replicas may be used for read fallback. Automatic monitoring covers storage, replication, ingestion, database and syslog-listener conditions, with persisted notification-delivery results.

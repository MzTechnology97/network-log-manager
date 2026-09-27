# Persistent data layout

Network Log Manager keeps permanent archives separate from disposable search cache.

- `/archive/mikrotik/YYYY/MM/`: permanent yearly archive. Each daily table is exported as `.sql.zst` with metadata and SHA-256 before the source table is removed.
- `/var/cache/netlog-manager/history/`: disposable historical-search cache reconstructed from the permanent archive.
- `/var/cache/netlog-manager/exports/`: generated exports with independent retention.
- `/var/lib/netlog-manager/`: application state.

The archive job runs daily. It only selects daily tables older than January 1 of the current year, so the current year remains online. The reference service processes up to ten eligible daily tables per run.

The archive directory is persistent data and must never be deleted by application upgrades. In Docker deployments it must be a bind mount or persistent volume. A NAS can later be used as a durable archive/export destination, but the search cache remains disposable.

# Architettura / Architecture

Componenti principali / Main components: syslog-ng → MariaDB `syslogdb`; FastAPI → `netlog_manager`; Nginx/Apache reverse proxy; archive cache; export worker; cleanup; archive retention.

Dati persistenti / Persistent data: MariaDB, external archive, application state. Cache ed export sono ricostruibili o temporanei secondo funzione. / Cache and exports are rebuildable or temporary according to their role.

Vedi / See: [docs/architecture.md](../docs/architecture.md).

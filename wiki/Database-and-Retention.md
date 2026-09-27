# Database e retention / Database and Retention

Le tabelle giornaliere `mikrotik_logs_YYYY_MM_DD` mantengono timestamp, sorgente, destinazione, protocollo e traduzione NAT. Le tabelle future vengono create dalla routine `CreateFutureTables`.

Daily tables store timestamp, source, destination, protocol and NAT translation. `CreateFutureTables` creates the rolling future tables.

Le tabelle degli anni precedenti sono archiviate in Zstandard con checksum; il DROP della sorgente avviene solo dopo le verifiche. / Prior-year tables are archived with Zstandard and checksums; source DROP occurs only after validation.

Vedi / See: [docs/database-retention.md](../docs/database-retention.md).

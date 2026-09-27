# Development and CI / Sviluppo e CI

## Italiano
Le modifiche passano da branch/PR. La CI controlla sintassi Bash, compilazione Python, migration, secret/private-key leakage, dipendenze, build Docker, sintassi syslog-ng e avvio end-to-end dello stack con MariaDB, app, syslog-ng e proxy HTTPS.

Le migration sono ordinate e immutabili dopo l'applicazione; `schema_migrations` conserva versione e SHA-256. Non modificare una migration già distribuita: crearne una nuova.

Gli aggiornamenti eseguono backup prima di migration/deploy e health-check dopo il deploy. Le migration DB sono forward-only e non vengono automaticamente invertite durante rollback software.

## English
Changes should flow through branches and pull requests. CI validates Bash/Python, migrations, secret/private-key leakage, dependencies, Docker builds, syslog-ng syntax, and an end-to-end MariaDB/application/syslog/proxy stack.

Applied migrations are immutable and tracked by version and SHA-256. Updates back up state before migration/deployment and run health checks afterward. Database migrations are forward-only and are not automatically reversed by software rollback.

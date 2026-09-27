# Configurazione / Configuration

I secret runtime non appartengono al repository. / Runtime secrets do not belong in the repository.

Variabili principali / Main variables: database hosts/users/passwords, `SECRET_KEY`, `ARCHIVE_ROOT`, `EXPORT_ROOT`, `LIVE_LOG`, hostname/TLS and syslog port.

Vedi / See: [docs/configuration.md](../docs/configuration.md).


Storage e notifiche / Storage and notifications: configurabili da **Impostazioni**. I target remoti vengono verificati write/read/SHA-256/delete prima dell'attivazione; i canali possono sottoscrivere singoli tipi di evento. / Remote targets are integrity-tested before activation and notification channels subscribe to selected event types.

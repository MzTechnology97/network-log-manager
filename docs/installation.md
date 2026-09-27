# Installation / Installazione

## Italiano
Network Log Manager supporta **Docker** (modalità raccomandata per nuove installazioni) e **Native/Hybrid** per sistemi Debian compatibili.

### Requisiti Docker
- host Linux con accesso root;
- Docker Engine e Docker Compose plugin;
- porte TCP 80/443 e porta syslog UDP/TCP scelta durante il bootstrap;
- spazio persistente per MariaDB e archivio storico.

Avvio:
```bash
sudo ./scripts/bootstrap.sh
```
Il bootstrap richiede modalità, hostname/IP, porta syslog, timezone e percorso archivio. Le credenziali interne sono generate automaticamente. L'account Administrator iniziale viene creato una sola volta.

I dati persistenti non devono essere cancellati durante gli aggiornamenti. L'archivio storico è esterno al ciclo di vita delle immagini Docker.

### Native
Lo stesso bootstrap permette la modalità native. Installa MariaDB, syslog-ng, Python/venv, Apache e unità systemd.

## English
Network Log Manager supports **Docker** (recommended for new deployments) and **Native/Hybrid** on compatible Debian systems.

Run `sudo ./scripts/bootstrap.sh`. The wizard asks for deployment mode, hostname/IP, syslog port, timezone and archive path. Internal credentials are generated automatically and the first Administrator is created only when none exists.

Persistent database volumes and the archive must never be deleted during normal software updates.
